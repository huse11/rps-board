"""
板块 RPS 计算引擎 v3.0
- 多Token轮询突破限频
- 全量历史 RPS 回溯（60天逐日计算）
- 排名变化、连续天数、60日RPS≥90统计
"""
import tushare as ts
import pandas as pd
import numpy as np
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

# ========================== 配置 ==========================
TUSHARE_TOKENS = [
    "3af69fb7165cd116e06a00ba666afbd71af530aca5bdc2d4b9d6ce08",
    "83e45faa47e13283017fc3db27f36d4bfd033827b566fb227d77d9d7",
    "d204a0e1d3a2d6f94e5843ba56b0afd1ae02a8658d81e985e1db2055",
    "ce54958918a51905315e6ab2b009dedca3ae849af3aa7732125b0642",
    "abc7ea5f14850f390d678129eadcac60b1ab8aabeb56abf8cfa3ac4c",
]
RPS_THRESHOLD = 90
SCHEMA_VERSION = 5  # v5=股票池剔除ST/*ST/退市 (幂等判断用, 改公式/格式/板块池需+1强制重算)
STATIC_DIR = Path(__file__).parent / "static"
DATA_FILE = STATIC_DIR / "rps_data.json"
LAST_RESULT_FILE = STATIC_DIR / "last_result.json"
RPS_HISTORY_FILE = STATIC_DIR / "rps_history.json"
RUN_STATUS_FILE = STATIC_DIR / "rps_run_status.json"
# ==========================================================


def write_run_status(success, date, mode="calc", message=""):
    """写 rps 运行状态文件 (rps_run_status.json), 供下游模块校验当日是否执行成功
    - 前端可据此提示"今日计算失败/过期数据", 而非静默展示旧结果
    - 周度推荐执行前校验: 当日 RPS 未成功则跳过, 避免拿旧 json 输出错误结果
    success: bool 当日 rps_data.json 是否完整可用(计算成功 或 幂等跳过但文件完整)
    mode:    "calc"(重新计算) / "skip"(幂等跳过) / "error"(失败)
    """
    try:
        status = {
            "update_date": date,
            "success": bool(success),
            "mode": mode,
            "message": message or "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(RUN_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        if not success:
            print(f"  ⚠️ 运行状态已标记失败: {RUN_STATUS_FILE.name}")
        return True
    except Exception as e:
        print(f"  ⚠️ 运行状态写入失败: {str(e)[:60]}")
        return False


def load_run_status():
    """读取 rps 运行状态; 文件缺失/解析失败 → None"""
    try:
        if RUN_STATUS_FILE.exists():
            with open(RUN_STATUS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def load_out_list_names(data):
    """从 rps_data.json 提取当日三档调出板块名集合 (供候选池全调出剔除)"""
    out = set()
    if not data:
        return out
    for k in ("rps5", "rps10", "rps20"):
        for rec in data.get(k, {}).get("out_list", []) or []:
            if rec and rec.get("name"):
                out.add(str(rec["name"]).strip())
    return out


def load_review_data():
    """读取 daily_review.json 复盘数据 (供推荐模块成熟疲劳/danger 板块评分扣分联动);
    文件缺失/解析失败 → None (不影响选股)"""
    try:
        f = STATIC_DIR / "daily_review.json"
        if f.exists():
            with open(f, encoding="utf-8") as fp:
                return json.load(fp)
    except Exception as e:
        print(f"  ⚠️ daily_review.json 读取失败(跳过复盘联动): {str(e)[:50]}")
    return None


class TusharePool:
    """多 Token 轮询池"""

    def __init__(self, tokens):
        self.pros = []
        for token in tokens:
            ts.set_token(token.strip())
            self.pros.append(ts.pro_api())
        self.idx = 0
        self._rate_limited = {}
        print(f"  ✅ Tushare 多Token池: {len(tokens)} 个")

    def _is_limited(self, idx, method):
        now = time.time()
        return self._rate_limited.get(idx, {}).get(method, 0) > now

    def _mark_limited(self, idx, method, seconds=3600):
        if idx not in self._rate_limited:
            self._rate_limited[idx] = {}
        self._rate_limited[idx][method] = time.time() + seconds

    def call(self, method_name, **kwargs):
        n = len(self.pros)
        errors = []
        for i in range(n):
            idx = self.idx
            if self._is_limited(idx, method_name):
                self.idx = (self.idx + 1) % n
                continue
            pro = self.pros[idx]
            try:
                method = getattr(pro, method_name)
                result = method(**kwargs)
                if result is not None and len(result) > 0:
                    self.idx = (idx + 1) % n
                    return result, idx
            except Exception as e:
                err_str = str(e)
                limited = any(kw in err_str for kw in ["频率超限", "限频", "次数超限"])
                if limited:
                    wait = 3600 if method_name in ("stock_basic", "trade_cal") else 60
                    self._mark_limited(idx, method_name, wait)
                    errors.append(f"Token{i+1} {method_name}限频({wait}s)")
                else:
                    errors.append(f"Token{i+1} {method_name}: {err_str[:50]}")
                self.idx = (self.idx + 1) % n
        raise Exception(f"所有Token均无法调用{method_name}:\n  " + "\n  ".join(errors))

    def call_any(self, method_name, **kwargs):
        result, _ = self.call(method_name, **kwargs)
        return result

    def status(self):
        for i in range(len(self.pros)):
            limits = self._rate_limited.get(i, {})
            if limits:
                now = time.time()
                remaining = {k: max(0, int(v - now)) for k, v in limits.items()}
                print(f"  Token{i+1}限频: {remaining}")
            else:
                print(f"  Token{i+1}正常")


os.makedirs(STATIC_DIR, exist_ok=True)
pool = TusharePool(TUSHARE_TOKENS)

# 代码前缀→板块映射（降级方案）
CODE_PREFIX_INDUSTRY = {
    "600":"沪市主板","601":"沪市主板","603":"沪市主板","605":"沪市主板",
    "000":"深市主板","001":"深市主板","002":"深市中小板","003":"深市中小板",
    "300":"创业板","301":"创业板","688":"科创板","689":"科创板",
    "8":"北交所","4":"老三板",
}


def _get_industry_from_code(ts_code):
    symbol = ts_code.split(".")[0]
    for prefix, industry in sorted(CODE_PREFIX_INDUSTRY.items(), key=lambda x: -len(x[0])):
        if symbol.startswith(prefix):
            return industry
    return "其他"


def get_trade_days(n=80):
    cache_file = STATIC_DIR / "trade_cal_cache.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            trade_days = json.load(f)
        if trade_days and trade_days[-1] >= datetime.now().strftime("%Y%m%d"):
            return trade_days[-n:]
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=150)).strftime("%Y%m%d")
    try:
        df, _ = pool.call("trade_cal", exchange="SSE", start_date=start, end_date=end)
        trade_days = df[df["is_open"] == 1]["cal_date"].tolist()
        trade_days.sort()
        with open(cache_file, "w") as f:
            json.dump(trade_days, f)
        return trade_days[-n:]
    except Exception:
        print("  ⚠️ trade_cal全Token受限，降级日期推算")
        all_days = pd.bdate_range(end=datetime.now(), periods=n+20, freq="B").strftime("%Y%m%d").tolist()
        return sorted(all_days)[-n:]


def _filter_st(df):
    """剔除 ST/*ST/退市 股票 (名称含 ST/退 关键词, 板块RPS/成分股/候选池统一过滤)"""
    if df is None or len(df) == 0:
        return df
    n0 = len(df)
    df = df[~df["name"].fillna("").str.contains(r"ST|退", regex=True, na=False)]
    if len(df) < n0:
        print(f"  ⏭️ 剔除ST/退市: {n0 - len(df)}只")
    return df


def get_stock_industry():
    """股票→行业映射。优先申万三级细分板块池(208个), 回退 Tushare stock_basic
    所有出口统一剔除 ST/*ST/退市 股票(名称含 ST/退)"""
    import sw_industry
    sw_map = sw_industry.build_sw_industry_map()
    if sw_map:
        cache_file = STATIC_DIR / "stock_basic_cache.json"
        old_basic = None
        if os.path.exists(cache_file):
            try:
                old_basic = pd.read_json(cache_file, dtype={"ts_code": str})
            except Exception:
                pass
        records = sw_industry.build_industry_df(sw_map, old_basic)
        if records:
            result = pd.DataFrame(records)
            result["fetch_date"] = datetime.now().strftime("%Y%m%d")
            result.to_json(cache_file, orient="records", force_ascii=False)
            print(f"  ✅ 申万三级板块池: {result['industry'].nunique()}个细分板块, {len(result)}只股票")
            return _filter_st(result[["ts_code", "name", "industry", "list_date"]])
    cache_file = STATIC_DIR / "stock_basic_cache.json"
    if os.path.exists(cache_file):
        df = pd.read_json(cache_file, dtype={"ts_code": str})
        # 缓存必须含 list_date 列(次新股过滤用), 且拉取日期不超过5天(否则重拉, 纳入新股/退市调整)
        if len(df) > 0 and "list_date" in df.columns:
            fd = str(df["fetch_date"].iloc[0]) if "fetch_date" in df.columns else ""
            days_old = (datetime.now() - datetime.strptime(fd, "%Y%m%d")).days if len(fd) == 8 else 999
            if days_old <= 5:
                return _filter_st(df[["ts_code", "name", "industry", "list_date"]])
    try:
        df, _ = pool.call("stock_basic", fields="ts_code,name,industry,list_date")
        df = df[df["industry"].notna() & (df["industry"] != "")]
        result = df[["ts_code", "name", "industry", "list_date"]].copy()
        result["list_date"] = result["list_date"].astype(str).fillna("")
        result["fetch_date"] = datetime.now().strftime("%Y%m%d")
        result.to_json(cache_file, orient="records", force_ascii=False)
        print(f"  ✅ stock_basic: {len(result)}只, {result['industry'].nunique()}个行业")
        return _filter_st(result[["ts_code", "name", "industry", "list_date"]])
    except Exception as e:
        print(f"  ⚠️ stock_basic全Token受限: {e}")
        # 回退: 旧版缓存(缺fetch_date但含真实行业映射)优于前缀推断, 复用并补日期戳
        # (服务器 Tushare 免费token 无 stock_basic 权限, 重拉必失败; 旧缓存是最近一次
        #  成功拉取的 110 个真实细分行业, 远好于代码前缀的 6 个粗分组)
        if os.path.exists(cache_file):
            try:
                df = pd.read_json(cache_file, dtype={"ts_code": str})
                if len(df) > 0 and "list_date" in df.columns and "industry" in df.columns:
                    result = df[["ts_code", "name", "industry", "list_date"]].copy()
                    result["list_date"] = result["list_date"].astype(str).fillna("")
                    result["fetch_date"] = datetime.now().strftime("%Y%m%d")
                    result.to_json(cache_file, orient="records", force_ascii=False)
                    print(f"  ✅ stock_basic缓存回退: {len(result)}只, {result['industry'].nunique()}个行业(真实映射)")
                    return _filter_st(result[["ts_code", "name", "industry", "list_date"]])
            except Exception:
                pass
    print("  ⚠️ 降级: 代码前缀推断板块")
    cache_dir = STATIC_DIR / "daily_cache"
    if cache_dir.exists():
        files = sorted(os.listdir(cache_dir))
        if files:
            df_daily = pd.read_json(cache_dir / files[-1], dtype={"ts_code": str})
            result = df_daily[["ts_code"]].copy()
            result["name"] = result["ts_code"].str.replace(r"\.(SZ|SH|BJ)$", "", regex=True)
            result["industry"] = result["ts_code"].apply(_get_industry_from_code)
            result["list_date"] = ""  # 降级时无上市日期, 次新股过滤将跳过
            print(f"  ✅ 前缀推断: {result['industry'].nunique()}个分组")
            return _filter_st(result[["ts_code", "name", "industry", "list_date"]])
    raise Exception("无法获取股票数据")


def fetch_daily_batch(trade_days):
    """批量获取日线数据（缓存 + 多Token）"""
    daily_map = {}
    total = len(trade_days)
    cache_dir = STATIC_DIR / "daily_cache"
    os.makedirs(cache_dir, exist_ok=True)
    for i, date in enumerate(trade_days):
        cache_path = cache_dir / f"{date}.json"
        if cache_path.exists():
            try:
                df = pd.read_json(cache_path, dtype={"ts_code": str})
                if len(df) > 0:
                    daily_map[date] = df
                    continue
            except Exception:
                pass
        try:
            df = pool.call_any("daily", trade_date=date)
            if df is not None and len(df) > 0:
                daily_map[date] = df
                df.to_json(cache_path, orient="records", force_ascii=False)
            print(f"  [{i+1}/{total}] {date}: {len(df) if df is not None else 0}条")
        except Exception as e:
            # 失败后重试2次（间隔3秒），应对临时限频
            retried = False
            for attempt in range(2):
                time.sleep(3)
                try:
                    df = pool.call_any("daily", trade_date=date)
                    if df is not None and len(df) > 0:
                        daily_map[date] = df
                        df.to_json(cache_path, orient="records", force_ascii=False)
                        print(f"  [{i+1}/{total}] {date}: 重试成功 {len(df)}条")
                        retried = True
                        break
                except Exception as e2:
                    print(f"  [{i+1}/{total}] {date}: 重试{attempt+1}失败 {str(e2)[:50]}")
            if not retried:
                print(f"  [{i+1}/{total}] {date}: {str(e)[:60]}")
        time.sleep(0.5)
    return daily_map


def calc_full_rps_history(daily_map, industry_df, trade_days):
    """
    计算全量 RPS 历史：
    1. 计算每日行业收益率
    2. 对每个交易日计算滚动 RPS5/10/20
    3. 计算排名变化、连续天数、60日RPS≥90
    4. 保存历史快照供下次对比
    
    返回: output (完整看板数据)
    """
    print("\n  [4a] 计算每日行业收益率...")
    # 每日行业收益率
    ind_records = []
    for date in trade_days:
        if date not in daily_map:
            continue
        df_day = daily_map[date]
        merged = df_day.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="inner")
        if len(merged) == 0:
            continue
        avg_chg = merged.groupby("industry")["pct_chg"].mean()
        for ind, chg in avg_chg.items():
            ind_records.append({"trade_date": date, "industry": ind, "daily_chg": chg})

    df_ind_daily = pd.DataFrame(ind_records)
    if df_ind_daily.empty:
        return None

    # 构建每个行业的收益率时间序列
    print("  [4b] 构建行业收益率时间序列...")
    industries = df_ind_daily["industry"].unique()
    total = len(industries)

    # 逐日计算滚动 RPS
    daily_rps_history = {}
    # 实际有数据的交易日（应对个别日期获取失败）
    available_days = [d for d in trade_days if d in daily_map]
    for i, date in enumerate(available_days[19:], 19):  # 从第20天开始(需要20日数据)
        # 获取截至该日、实际有数据的前20个交易日
        date_idx = available_days.index(date)
        lookback = available_days[max(0, date_idx-19):date_idx+1]
        
        day_scores = {}
        for ind in industries:
            ind_data = df_ind_daily[(df_ind_daily["industry"] == ind) & 
                                     (df_ind_daily["trade_date"].isin(lookback))]
            ind_data = ind_data.sort_values("trade_date")
            chgs = ind_data["daily_chg"].values / 100.0
            
            scores = {}
            for period, col in [(5, "chg5"), (10, "chg10"), (20, "chg20")]:
                if len(chgs) >= period:
                    recent = chgs[-period:]
                    cum_ret = np.prod(1 + recent) - 1
                    scores[col] = cum_ret * 100
                else:
                    scores[col] = None
            day_scores[ind] = scores

        # 转 DataFrame 计算 RPS
        df_day_rps = pd.DataFrame(day_scores).T
        df_day_rps.index.name = "industry"
        # 欧奈尔体系RPS(标准原版): RPS = (1 - 涨幅排名/全市场有效板块总数) * 100
        # 涨幅从高到低排名(涨幅最高→排名1); RPS=95 表示跑赢全市场95%的板块; 第1名≈100, 最后一名≈0
        total_ind = len(df_day_rps) if len(df_day_rps) > 0 else 1
        for period, col in [(5, "RPS5"), (10, "RPS10"), (20, "RPS20")]:
            chg_col = f"chg{period}"
            if chg_col in df_day_rps.columns and df_day_rps[chg_col].notna().sum() > 0:
                # pandas rank 从1开始(ascending=False 涨幅最高→1), 与标准公式 RPS=(1-RANK/TOTAL)*100 直接对应
                df_day_rps[f"rank{period}"] = df_day_rps[chg_col].rank(ascending=False)
                df_day_rps[col] = (1 - df_day_rps[f"rank{period}"] / total_ind) * 100

        # 存储该日历史
        daily_rps_history[date] = {
            ind: {
                "chg5": _safe_get(df_day_rps, ind, "chg5"),
                "chg10": _safe_get(df_day_rps, ind, "chg10"),
                "chg20": _safe_get(df_day_rps, ind, "chg20"),
                "RPS5": _safe_get(df_day_rps, ind, "RPS5"),
                "RPS10": _safe_get(df_day_rps, ind, "RPS10"),
                "RPS20": _safe_get(df_day_rps, ind, "RPS20"),
                "rank5": _safe_get_int(df_day_rps, ind, "rank5"),
                "rank10": _safe_get_int(df_day_rps, ind, "rank10"),
                "rank20": _safe_get_int(df_day_rps, ind, "rank20"),
            }
            for ind in industries if ind in df_day_rps.index
        }

        if (i - 19 + 1) % 10 == 0:
            print(f"    [{i-19+1}/{len(trade_days)-19}] {date} - {len(industries)}个行业")

    today = available_days[-1]
    prev_day = available_days[-2] if len(available_days) >= 2 else None

    # 获取当日 RPS 数据
    today_rps = daily_rps_history.get(today, {})
    prev_rps = daily_rps_history.get(prev_day, {}) if prev_day else {}

    print("  [4c] 计算排名变化、连续天数、60日RPS≥90...")
    
    # 构建结果
    result_list = []
    for ind in industries:
        t_rps = today_rps.get(ind, {})
        p_rps = prev_rps.get(ind, {})
        
        rps5 = t_rps.get("RPS5")
        rps10 = t_rps.get("RPS10")
        rps20 = t_rps.get("RPS20")
        chg5 = t_rps.get("chg5")
        chg10 = t_rps.get("chg10")
        chg20 = t_rps.get("chg20")
        rank5 = t_rps.get("rank5")
        rank10 = t_rps.get("rank10")
        rank20 = t_rps.get("rank20")
        prev_rank5 = p_rps.get("rank5")
        prev_rank10 = p_rps.get("rank10")
        prev_rank20 = p_rps.get("rank20")

        # 排名变化（+上升 -下降）
        rank_chg5 = (prev_rank5 - rank5) if (prev_rank5 is not None and rank5 is not None) else None
        rank_chg10 = (prev_rank10 - rank10) if (prev_rank10 is not None and rank10 is not None) else None
        rank_chg20 = (prev_rank20 - rank20) if (prev_rank20 is not None and rank20 is not None) else None

        # 连续天数：从今天往前数，连续 RPS ≥ 阈值的交易日
        con5 = calc_consecutive(daily_rps_history, trade_days, ind, "RPS5", today)
        con10 = calc_consecutive(daily_rps_history, trade_days, ind, "RPS10", today)
        con20 = calc_consecutive(daily_rps_history, trade_days, ind, "RPS20", today)

        # 60日RPS≥90统计：过去60个交易日中，RPS ≥ 阈值的次数
        cnt5 = count_above_threshold(daily_rps_history, trade_days, ind, "RPS5", today, 60)
        cnt10 = count_above_threshold(daily_rps_history, trade_days, ind, "RPS10", today, 60)
        cnt20 = count_above_threshold(daily_rps_history, trade_days, ind, "RPS20", today, 60)

        result_list.append({
            "industry": ind,
            "RPS5": rps5, "RPS10": rps10, "RPS20": rps20,
            "chg5": chg5, "chg10": chg10, "chg20": chg20,
            "rank5": rank5, "rank10": rank10, "rank20": rank20,
            "rank_chg5": rank_chg5, "rank_chg10": rank_chg10, "rank_chg20": rank_chg20,
            "consecutive5": con5, "consecutive10": con10, "consecutive20": con20,
            "days90_5": cnt5, "days90_10": cnt10, "days90_20": cnt20,
        })

    df_result = pd.DataFrame(result_list)
    return df_result, daily_rps_history


def _safe_get(df, ind, col):
    """安全取值：列不存在或值为NaN时返回None，避免KeyError"""
    if col in df.columns and ind in df.index:
        val = df.loc[ind, col]
        if pd.notna(val):
            return float(val)
    return None


def _safe_get_int(df, ind, col):
    """安全取整数值：列不存在或值为NaN时返回None，避免KeyError"""
    if col in df.columns and ind in df.index:
        val = df.loc[ind, col]
        if pd.notna(val):
            return int(val)
    return None


def calc_consecutive(rps_history, trade_days, industry, rps_col, today):
    """计算连续 RPS≥90 的天数"""
    dates = sorted(rps_history.keys())
    if today not in rps_history:
        return 0
    ind_rps = rps_history[today].get(industry, {}).get(rps_col)
    if ind_rps is None or ind_rps < RPS_THRESHOLD:
        return 0
    count = 1
    for date in reversed(dates):
        if date >= today:
            continue
        val = rps_history[date].get(industry, {}).get(rps_col)
        if val is not None and val >= RPS_THRESHOLD:
            count += 1
        else:
            break
    return count


def count_above_threshold(rps_history, trade_days, industry, rps_col, today, lookback):
    """统计过去N个交易日中 RPS ≥ 阈值的次数"""
    dates = sorted(rps_history.keys())
    count = 0
    for date in dates:
        if date > today:
            continue
        val = rps_history[date].get(industry, {}).get(rps_col)
        if val is not None and val >= RPS_THRESHOLD:
            count += 1
    return count


def calc_stock_rps50_by_industry(daily_map, industry_df, trade_days):
    """
    计算每只个股的RPS50，按行业聚合：总成分股数 + RPS50≥90计数
    """
    if len(trade_days) < 50:
        print("  ⚠️ 交易日不足50，跳过RPS50计算")
        return {}

    # 使用实际有数据的交易日（应对个别日期获取失败）
    available = [d for d in trade_days if d in daily_map]
    if len(available) < 50:
        print(f"  ⚠️ 有效交易日{len(available)}不足50，跳过RPS50计算")
        return {}
    recent_50 = available[-50:]
    print(f"  [4d] 个股RPS50计算（{len(recent_50)}个交易日, {len(industry_df)}只股票）...")

    # 收集每只股票的每日涨跌幅
    stock_returns = {}
    for date in recent_50:
        if date not in daily_map:
            continue
        df = daily_map[date]
        for _, row in df.iterrows():
            code = row["ts_code"]
            chg = row.get("pct_chg")
            if chg is not None and not pd.isna(chg):
                if code not in stock_returns:
                    stock_returns[code] = []
                stock_returns[code].append(chg / 100.0)

    # 计算50日累计收益
    stock_cum_ret = {}
    valid_count = 0
    for code, returns in stock_returns.items():
        if len(returns) >= 50:
            cum_ret = float(np.prod(1 + np.array(returns[-50:]))) - 1
            stock_cum_ret[code] = cum_ret * 100
            valid_count += 1
    print(f"    有效股票: {valid_count}只（有完整50日数据）")

    if not stock_cum_ret:
        return {}

    # RPS50 = (1 - 涨幅排名/全市场有效股票总数) * 100 (欧奈尔标准: 涨幅最高→排名1)
    df_stock = pd.DataFrame(list(stock_cum_ret.items()), columns=["ts_code", "chg50"])
    total_stock = len(df_stock) if len(df_stock) > 0 else 1
    df_stock["rank50"] = df_stock["chg50"].rank(ascending=False)
    df_stock["RPS50"] = (1 - df_stock["rank50"] / total_stock) * 100

    # 合并行业
    merged = df_stock.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="inner")

    # 聚合
    result = {}
    for ind, grp in merged.groupby("industry"):
        total = len(grp)
        above = int((grp["RPS50"] >= 90).sum())
        result[ind] = {"stock_count": total, "rps50_above_90": above}
    print(f"    完成: {len(result)}个行业聚合")
    return result


def calc_industry_rps50_60day_count(daily_map, industry_df, trade_days):
    """
    计算每个行业近60个交易日中，行业RPS50 ≥ 90的天数
    行业RPS50 = 行业50日收益率在全行业的百分位排名
    """
    if len(trade_days) < 60:
        print("  ⚠️ 交易日不足60，跳过行业RPS50天数统计")
        return {}

    # 使用实际有数据的交易日（应对个别日期获取失败）
    available = [d for d in trade_days if d in daily_map]
    if len(available) < 50:
        print(f"  ⚠️ 有效交易日{len(available)}不足50，跳过行业RPS50天数统计")
        return {}

    print(f"  [4e] 行业RPS50逐日回溯（{len(available)}个有效交易日）...")

    # 1. 计算每日行业收益率
    ind_records = []
    for date in available:
        df_day = daily_map[date]
        merged = df_day.merge(industry_df[["ts_code", "industry"]], on="ts_code", how="inner")
        if len(merged) == 0:
            continue
        avg_chg = merged.groupby("industry")["pct_chg"].mean()
        for ind, chg in avg_chg.items():
            ind_records.append({"trade_date": date, "industry": ind, "daily_chg": chg})

    df_ind_daily = pd.DataFrame(ind_records)
    if df_ind_daily.empty:
        return {}

    industries = df_ind_daily["industry"].unique()
    total_ind = len(industries)

    # 2. 对每个可计算的交易日，计算行业50日收益 → RPS50
    # 需要至少50个交易日数据才能计算
    start_idx = 49  # index 49 = 第50个交易日
    daily_rps50 = {}  # {date: {industry: rps50_value}}

    for i, date in enumerate(available[start_idx:], start_idx):
        lookback = available[max(0, i - 49):i + 1]

        day_scores = {}
        for ind in industries:
            ind_data = df_ind_daily[(df_ind_daily["industry"] == ind) &
                                     (df_ind_daily["trade_date"].isin(lookback))]
            ind_data = ind_data.sort_values("trade_date")
            chgs = ind_data["daily_chg"].values / 100.0
            if len(chgs) >= 50:
                cum_ret = float(np.prod(1 + chgs[-50:])) - 1
                day_scores[ind] = cum_ret * 100
            else:
                day_scores[ind] = None

        # RPS50 = (1 - 涨幅排名/全行业总数) * 100 (欧奈尔标准)
        df_day = pd.DataFrame(list(day_scores.items()), columns=["industry", "chg50"])
        total_ind50 = len(df_day) if len(df_day) > 0 else 1
        df_day["RPS50"] = (1 - df_day["chg50"].rank(ascending=False) / total_ind50) * 100

        daily_rps50[date] = {
            row["industry"]: float(row["RPS50"])
            for _, row in df_day.iterrows()
            if row["RPS50"] is not None and not pd.isna(row["RPS50"])
        }

        if (i - start_idx + 1) % 10 == 0:
            print(f"    [{i-start_idx+1}/{len(available)-start_idx}] {date} RPS50计算完成")

    # 3. 统计最近60个交易日中 RPS50 ≥ 90 的天数
    # 60日窗口 = 最近60个有效交易日（若不足60则用全部）
    window = available[-60:]
    count_results = {}
    window_dates = [d for d in window if d in daily_rps50]

    for ind in industries:
        count_90 = 0
        for date in window_dates:
            rps50 = daily_rps50[date].get(ind)
            if rps50 is not None and rps50 >= 90:
                count_90 += 1
        count_results[ind] = count_90

    print(f"    完成: {len(count_results)}个行业统计（窗口{len(window_dates)}天）")
    return count_results


def split_in_out(df_rps, threshold, rps_col, rank_col, rank_chg_col, consec_col, stock_rps50, industry_rps50_count, last_in_names):
    """拆分入选/调出，标记状态，生成看板字段"""
    df_in = df_rps[df_rps[rps_col].notna() & (df_rps[rps_col] >= threshold)].copy()
    df_in = df_in.sort_values(rps_col, ascending=False).reset_index(drop=True)
    now_in_names = set(df_in["industry"].tolist())

    out_names = last_in_names - now_in_names
    df_out = df_rps[df_rps["industry"].isin(out_names)].copy()
    df_out = df_out.sort_values(rps_col, ascending=False).reset_index(drop=True)

    def make_records(df, status_val):
        records = []
        for _, row in df.iterrows():
            ind = row["industry"]
            sr = (stock_rps50 or {}).get(ind, {})
            records.append({
                "name": ind,
                "status": status_val,
                rps_col: round(row[rps_col], 1) if pd.notna(row.get(rps_col)) else None,
                "chg5": round(row["chg5"], 2) if pd.notna(row.get("chg5")) else None,
                "chg10": round(row["chg10"], 2) if pd.notna(row.get("chg10")) else None,
                "chg20": round(row["chg20"], 2) if pd.notna(row.get("chg20")) else None,
                "rank": int(row[rank_col]) if pd.notna(row.get(rank_col)) else None,
                "rank_change": int(row[rank_chg_col]) if pd.notna(row.get(rank_chg_col)) else 0,
                "continuous": int(row[consec_col]) if pd.notna(row.get(consec_col)) else 0,
                "days_60": int(industry_rps50_count.get(ind, 0)) if industry_rps50_count else 0,
                "stock_count": int(sr.get("stock_count", 0)),
                "rps50_above_90": int(sr.get("rps50_above_90", 0)),
            })
        return records

    # 标记状态
    in_records = make_records(df_in, "在榜")
    for rec in in_records:
        if rec["name"] not in last_in_names:
            rec["status"] = "调入"
    out_records = make_records(df_out, "调出")

    return {
        "in_list": in_records,
        "out_list": out_records,
        "current_names": list(now_in_names),
    }


def calc_expected_trade_date(trade_days):
    """
    期望数据交易日 = 最近一个已收盘的交易日（北京时区15:00后视为已收盘）
    解决 cron 延迟/每日补跑时误用未来交易日导致的新鲜度误报
    """
    now_bj = datetime.utcnow() + timedelta(hours=8)
    today = now_bj.strftime("%Y%m%d")
    hour_bj = now_bj.hour
    for d in reversed(sorted(trade_days)):
        if d < today:
            return d
        if d == today and hour_bj >= 15:
            return d
    return sorted(trade_days)[-1]


# ================== 实时快照纯函数（可单元测试）==================
def em_secid(ts_code):
    """ts_code(如 600000.SH) → 东方财富secid(1.=沪 0.=深/北)"""
    symbol, mkt = ts_code.split(".")
    return ("1." if mkt == "SH" else "0.") + symbol


def tx_prefix(ts_code):
    """ts_code → 腾讯行情前缀(sh/sz/bj)"""
    symbol, mkt = ts_code.split(".")
    return "sh" if mkt == "SH" else ("bj" if mkt == "BJ" else "sz")


def em_mkt_from_f13(f13, symbol):
    """
    东财f13(0=深/北 1=沪 83=北) + 代码前缀 → 市场后缀(SH/SZ/BJ)
    回归保护: 东财北交所 f13=0(同深市), 需按代码前缀(4/8/920)识别为北交所
    """
    mkt = {0: "SZ", 1: "SH", 83: "BJ"}.get(f13)
    if not mkt:
        return None
    if mkt == "SZ" and str(symbol).startswith(("4", "8", "920")):
        mkt = "BJ"
    return mkt


def bj_date(ts):
    """东财f124时间戳(UTC秒) → 北京时间YYYYMMDD"""
    if not ts:
        return ""
    return (datetime.utcfromtimestamp(float(ts)) + timedelta(hours=8)).strftime("%Y%m%d")


def normalize_rt_fields(bid, ask, vol_now, speed):
    """
    四列字段归一化（回归保护: 避免四列意外变空/语义错误）
    - 买卖价: 无效值/<=0 → None（涨停无卖一、跌停无买一属正常）
    - 现量: 东财带方向符号, 取绝对值(手数)
    - 涨速: 收盘后为0也保留(0是正常值, 不是缺失)
    """
    if bid in (None, "-") or float(bid) <= 0:
        bid = None
    if ask in (None, "-") or float(ask) <= 0:
        ask = None
    if vol_now in (None, "-"):
        vol_now = None
    else:
        vol_now = abs(float(vol_now))
    if speed in (None, "-"):
        speed = None
    return bid, ask, vol_now, speed


def fetch_realtime_quotes(codes, latest_date):
    """
    获取个股实时快照: 买价(bid)/卖价(ask)/现量(vol_now)/涨速(speed)
    优先东方财富 ulist(批量, 含涨速/现量), 失败降级腾讯 qt.gtimg.cn(仅买/卖价)
    仅当快照交易日 == latest_date 时才填充, 否则保持 None(避免跨日数据错配)
    返回 {ts_code: {"bid":..,"ask":..,"vol_now":..,"speed":..}}
    """
    import urllib.request
    result = {c: {"bid": None, "ask": None, "vol_now": None, "speed": None} for c in codes}
    if not codes:
        return result
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
          "Referer": "https://quote.eastmoney.com/"}

    def get_json(url):
        req = urllib.request.Request(url, headers=ua)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    filled = set()
    # ---- 1. 东方财富（涨速/现量/买一/卖一）----
    # 域名池: 标准push2 → 延迟行情(push2delay, 稳定可用) → 数字子域
    EM_DOMAINS = (["push2.eastmoney.com", "push2delay.eastmoney.com"]
                  + [f"{n}.push2.eastmoney.com" for n in range(1, 6)])
    try:
        secids = [em_secid(c) for c in codes]

        def parse_em(d):
            diff = (d.get("data") or {}).get("diff", [])
            if isinstance(diff, dict):
                diff = [diff]
            got = 0
            for item in diff:
                symbol = str(item.get("f12"))
                mkt = em_mkt_from_f13(item.get("f13"), symbol)
                if not mkt:
                    continue
                ts_code = symbol + "." + mkt
                if ts_code not in result:
                    continue
                if bj_date(item.get("f124")) != latest_date:
                    continue  # 快照交易日与数据交易日不一致，丢弃
                bid, ask, vol_now, speed = normalize_rt_fields(
                    item.get("f31"), item.get("f32"), item.get("f30"), item.get("f22"))
                result[ts_code].update({"bid": bid, "ask": ask, "vol_now": vol_now, "speed": speed})
                filled.add(ts_code)
                got += 1
            return got

        # 主循环: 每批20只, 域名池轮换直到成功
        for i in range(0, len(secids), 20):
            chunk = secids[i:i + 20]
            d = None
            for dom in EM_DOMAINS:
                url = ("https://" + dom + "/api/qt/ulist.np/get?fltt=2&secids=" + ",".join(chunk)
                       + "&fields=f2,f12,f13,f22,f30,f31,f32,f124")
                try:
                    d = get_json(url)
                    if (d.get("data") or {}).get("diff"):
                        break
                except Exception:
                    d = None
                time.sleep(0.2)
            if d is not None:
                parse_em(d)
            time.sleep(0.15)

        # 补偿: 对未填充的按10只小批重试（优先稳定域名）
        missing_em = [c for c in codes if c not in filled]
        if missing_em:
            miss_secids = [em_secid(c) for c in missing_em]
            for i in range(0, len(miss_secids), 10):
                chunk = miss_secids[i:i + 10]
                for dom in reversed(EM_DOMAINS):
                    url = ("https://" + dom + "/api/qt/ulist.np/get?fltt=2&secids=" + ",".join(chunk)
                           + "&fields=f2,f12,f13,f22,f30,f31,f32,f124")
                    try:
                        d = get_json(url)
                        if d is not None and parse_em(d) > 0:
                            break
                    except Exception:
                        pass
                    time.sleep(0.15)
        print(f"  ✅ 东财快照: {len(filled)}只")
    except Exception as e:
        print(f"  ⚠️ 东财快照失败: {str(e)[:80]}")

    # ---- 2. 腾讯降级（仅买一/卖一，分批防URL超长）----
    missing = [c for c in codes if c not in filled]
    if missing:
        try:
            filled_tx = 0
            for i in range(0, len(missing), 100):
                chunk = missing[i:i + 100]
                codes_q = ",".join(tx_prefix(c) + c.split(".")[0] for c in chunk)
                url = "https://qt.gtimg.cn/q=" + codes_q
                req = urllib.request.Request(url, headers={"User-Agent": ua["User-Agent"]})
                with urllib.request.urlopen(req, timeout=10) as r:
                    raw = r.read().decode("gbk", errors="ignore")
                for m in re.finditer(r'v_(sh|sz|bj)(\d+)="([^"]*)"', raw):
                    mkt = "SH" if m.group(1) == "sh" else ("BJ" if m.group(1) == "bj" else "SZ")
                    code = m.group(2) + "." + mkt
                    if code not in result:
                        continue
                    parts = m.group(3).split("~")
                    if len(parts) < 31 or not parts[30]:
                        continue
                    if parts[30][:8] != latest_date:
                        continue
                    if parts[9] and float(parts[9]) > 0:
                        result[code]["bid"] = float(parts[9])
                        filled_tx += 1
                    if parts[19] and float(parts[19]) > 0:
                        result[code]["ask"] = float(parts[19])
                time.sleep(0.3)
            print(f"  ✅ 腾讯快照(买/卖价): {filled_tx}只")
        except Exception as e:
            print(f"  ⚠️ 腾讯快照失败: {str(e)[:80]}")

    n_filled = sum(1 for v in result.values() if v["bid"] is not None or v["speed"] is not None)
    print(f"  ✅ 实时快照填充: {n_filled}/{len(codes)}只（交易日 {latest_date}）")
    return result


def build_sector_stocks(industry_df, daily_map, latest_date, target_industries):
    """
    为行业板块构建成分股列表（含最近交易日行情）
    字段: 代码/名称/涨幅/现价/连涨天/涨跌/买价/卖价/总量/现量/涨速/换手/今开/最高/最低/昨收/量比/细分行业
    买价/卖价/现量/涨速 来自东方财富/腾讯实时快照（Tushare免费版无实时行情）
    """
    result = {ind: [] for ind in target_industries}
    members = industry_df[industry_df["industry"].isin(target_industries)]
    if members.empty:
        return result
    if latest_date not in daily_map:
        return result

    def fround(v, nd=2):
        return round(float(v), nd) if v is not None and not pd.isna(v) else None

    df_latest = daily_map[latest_date]
    latest_idx = {r["ts_code"]: r for _, r in df_latest.iterrows()}

    # 换手率/量比（daily_basic，全市场一次调用）
    basic_idx = {}
    try:
        df_basic = pool.call_any("daily_basic", trade_date=latest_date)
        if df_basic is not None and len(df_basic) > 0:
            basic_idx = {r["ts_code"]: r for _, r in df_basic.iterrows()}
        print(f"  ✅ daily_basic: {len(basic_idx)}条")
    except Exception as e:
        print(f"  ⚠️ daily_basic失败(换手率/量比将为空): {str(e)[:60]}")

    # 连涨天数：最近21个有效交易日逐股统计
    avail = sorted(daily_map.keys())
    recent = avail[-21:]
    target_codes = set(members["ts_code"])
    chg_by_code = {c: [] for c in target_codes}
    for date in recent:
        df = daily_map[date]
        sub = df[df["ts_code"].isin(target_codes)][["ts_code", "pct_chg"]]
        for _, r in sub.iterrows():
            c = r["ts_code"]
            v = r.get("pct_chg")
            if v is not None and not pd.isna(v):
                chg_by_code[c].append(float(v))

    # 实时快照（买价/卖价/现量/涨速，东财优先腾讯降级）
    rt_map = fetch_realtime_quotes(list(target_codes), latest_date)

    def consec_up(chgs):
        n = 0
        for v in reversed(chgs):
            if v > 0:
                n += 1
            else:
                break
        return n

    # 组装成分股（按涨幅降序）
    for ind in target_industries:
        grp = members[members["industry"] == ind]
        stocks = []
        for _, m in grp.iterrows():
            code = m["ts_code"]
            lr = latest_idx.get(code)
            if lr is None:
                continue
            br = basic_idx.get(code)
            rt = rt_map.get(code, {})
            stocks.append({
                "ts_code": code,
                "name": str(m["name"]),
                "industry": ind,
                "pct_chg": fround(lr.get("pct_chg")),
                "price": fround(lr.get("close")),
                "consec_up": consec_up(chg_by_code.get(code, [])),
                "change": fround(lr.get("close") - lr.get("pre_close")),
                "vol": int(lr["vol"]) if pd.notna(lr.get("vol")) else None,
                "turnover": fround(br.get("turnover_rate")) if br is not None else None,
                "open": fround(lr.get("open")),
                "high": fround(lr.get("high")),
                "low": fround(lr.get("low")),
                "pre_close": fround(lr.get("pre_close")),
                "vol_ratio": fround(br.get("volume_ratio")) if br is not None else None,
                "bid": rt.get("bid"),
                "ask": rt.get("ask"),
                "vol_now": rt.get("vol_now"),
                "speed": rt.get("speed"),
            })
        stocks.sort(key=lambda s: s["pct_chg"] if s["pct_chg"] is not None else -9999, reverse=True)
        result[ind] = stocks

    total = sum(len(v) for v in result.values())
    print(f"  ✅ 成分股: {len(result)}个板块, {total}只股票")
    return result


def _current_iso_week(dt=None):
    """本周 ISO 周标识 (如 2026W32), 用于周度推荐每周一次幂等判断"""
    dt = dt or datetime.now()
    iso = dt.isocalendar()
    return f"{iso[0]}W{iso[1]}"


def build_potential_stats(rps_history, stock_rps50=None, lo=88.0, hi=None):
    """临界潜力板块(RPS5∈[lo, hi))成分股统计 → 供 daily_review.potential_sectors 确认达标率
    边界板块(RPS 接近阈值但未入选, 如 RPS5∈[88,90)) 不在 rps_data.json 的
    in_list/out_list 中(无成分股 stocks), 其内部达标率无法在 daily_review 自行计算
    → 由 rps_calc 在 main() 中根据 daily_map 聚合个股 RPS50 后传入
    返回 [{name, stock_count, rps50_above_90, rank5, rank_change, continuous}]
    rank_change: 排名变化(正值=排名上升, 与 df_rps 的 rank_chg5 约定一致)
    continuous:  连续 RPS5 ≥ lo 的天数(在潜力区间蓄势多久)
    """
    hi = hi or RPS_THRESHOLD
    if not rps_history:
        return []
    dates = sorted(rps_history.keys())
    today = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else None
    out = []
    for ind, v in rps_history[today].items():
        r = v.get("RPS5")
        if r is None or pd.isna(r) or not (lo <= float(r) < hi):
            continue
        st = (stock_rps50 or {}).get(ind) or {}
        rank5 = v.get("rank5")
        prev_rank = rps_history[prev].get(ind, {}).get("rank5") if prev else None
        rank_chg = (prev_rank - rank5) if (rank5 is not None and prev_rank is not None) else None
        cont = 0
        for d in reversed(dates):  # 从今天向前连续 RPS5 ≥ lo
            dv = rps_history[d].get(ind, {}).get("RPS5")
            if dv is not None and not pd.isna(dv) and float(dv) >= lo:
                cont += 1
            else:
                break
        out.append({
            "name": ind,
            "stock_count": int(st.get("stock_count") or 0),
            "rps50_above_90": int(st.get("rps50_above_90") or 0),
            "rank5": float(rank5) if rank5 is not None else None,
            "rank_change": float(rank_chg) if rank_chg is not None else None,
            "continuous": cont,
        })
    out.sort(key=lambda x: -(x["rank5"] if x["rank5"] is not None else 0))
    return out


def _maybe_run_weekly(industry_df, daily_map, latest_date, prev_date, rps20_records,
                      out_list_names=None, review_data=None):
    """周度推荐(中线波段): 每周五收盘后更新 1 次
    幂等: 周一~周四本周已生成则跳过(以上周五收盘版为准); 周五强制用当日最新收盘数据重新生成
    (修复: 旧逻辑按 ISO 周判幂等, 周一生成后周五被误跳过, 导致周度推荐停留周一数据)
    状态校验: 当日 rps_calc 未执行成功(success=False 或 update_date≠latest_date) → 跳过周推荐,
    避免周五 RPS 计算失败仍拿旧 json 输出错误结果。
    rps20_records: RPS20 档位入选板块记录(含 name + continuous + stocks 成分股)
    """
    if not rps20_records:
        print("  ⏭️ 无 RPS20 入选板块, 跳过周度推荐")
        return False
    # 状态校验: rps_calc 当日成功是周推荐的前置条件 (防止周五 RPS 失败仍输出旧结果)
    _st = load_run_status()
    if _st is None or not _st.get("success") or str(_st.get("update_date", "")) != str(latest_date):
        _d = _st.get("update_date", "无") if _st else "无"
        print(f"  ⏭️ 当日 RPS 计算状态异常(date={_d}, success={_st.get('success') if _st else '未知'}), "
              f"跳过周度推荐(避免基于过期/损坏 rps_data 输出)")
        return False
    try:
        import recommend_weekly
        run_mode = recommend_weekly.CONFIG_WEEKLY.get("run_mode", {})
        orig_on = run_mode.get("ORIGINAL_MOMENTUM", True)
        early_on = run_mode.get("EARLY_STAGE_START", True)
        # 幂等判断: 原版周度文件存在则基于它; 原版关闭时基于早期文件
        wk_file = recommend_weekly.WEEKLY_REC_FILE if orig_on else recommend_weekly.WEEKLY_EARLY_REC_FILE
        if wk_file.exists():
            try:
                with open(wk_file, encoding="utf-8") as f:
                    _wk = json.load(f)
                # 周一~周四(weekday 0-3): 本周已有结果则跳过; 周五(weekday 4): 重新生成本周最终版
                if datetime.now().weekday() != 4 and _wk.get("week") == _current_iso_week():
                    print("  ⏭️ 周度推荐本周已生成, 跳过(每周五收盘后更新)")
                    return True
            except Exception:
                pass
        if orig_on:
            rec_list = recommend_weekly.recommend_weekly_stocks(
                industry_df, daily_map, latest_date, rps20_records,
                out_list_names=out_list_names, review_data=review_data)
            recommend_weekly.save_weekly_recommendations(rec_list, latest_date, prev_date)
        if early_on:
            rec_early = recommend_weekly.recommend_weekly_early_stocks(
                industry_df, daily_map, latest_date, rps20_records,
                out_list_names=out_list_names, review_data=review_data)
            recommend_weekly.save_weekly_early_recommendations(rec_early, latest_date, prev_date)
        return True
    except Exception as e:
        print(f"  ⚠️ 周度推荐生成失败(不阻断主流程): {str(e)[:80]}")
        return False


def _maybe_run_review(potential_stats=None):
    """每日复盘(动量视角量化复盘): 读取 rps_data/rps_history 生成 json+md, 失败不阻断主流程
    potential_stats: 临界潜力板块成分股统计(build_potential_stats 产出), 供早期启动挖掘
    daily_review.main() 自带幂等: 当日已生成则跳过
    """
    try:
        import daily_review
        daily_review.main(force=False, potential_stats=potential_stats)
    except Exception as e:
        print(f"  ⚠️ 每日复盘生成失败(不阻断主流程): {str(e)[:80]}")


def main():
    print("=" * 55)
    print("  A股板块 RPS 引擎 v3.0（全量历史回溯）")
    print("=" * 55)

    print("\n[1/5] 交易日历...")
    trade_days = get_trade_days(n=60)
    print(f"  {len(trade_days)} 个交易日")

    # 幂等保险：当天数据已生成且当天已完整计算过则跳过计算
    # （防止多个cron/手动触发重复刷新，覆盖last_result对比基准导致调入/调出错乱）
    latest_td = trade_days[-1]
    skip = False
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                exist = json.load(f)
            last_date = ""
            if os.path.exists(LAST_RESULT_FILE):
                with open(LAST_RESULT_FILE, "r", encoding="utf-8") as f:
                    last_date = str(json.load(f).get("date", ""))
            if (str(exist.get("update_date", "")) == latest_td
                    and int(exist.get("schema_version", 0)) >= SCHEMA_VERSION
                    and last_date == latest_td):
                skip = True
        except Exception:
            pass
    if skip:
        print(f"  ⏭️ 数据已是最新交易日 {latest_td} 且当天已完整计算，跳过计算（保持调入/调出对比基准）")
        # 幂等跳过但 rps_data.json 完整可用 → 标记运行成功 (下游可正常消费)
        write_run_status(True, latest_td, mode="skip", message="数据已最新, 幂等跳过计算")
        # 每日推荐与 RPS 计算解耦: 即使 RPS 被幂等跳过, 仍独立加载 daily_map 生成推荐
        # (recommend 不修改 rps_data.json/last_result.json, 不影响调入/调出对比基准)
        try:
            import recommend
            ind_df = get_stock_industry()
            dm = fetch_daily_batch(trade_days)
            avail = [d for d in trade_days if d in dm]
            ld = avail[-1] if avail else latest_td
            pd_ = avail[-2] if len(avail) >= 2 else ""
            in_list_records = []
            if DATA_FILE.exists():
                with open(DATA_FILE, encoding="utf-8") as f:
                    _data = json.load(f)
                for _k in ("rps5", "rps10", "rps20"):
                    # 完整入选板块记录(含 name + stocks 成分股), 供板块共振计数
                    in_list_records.extend(_data.get(_k, {}).get("in_list", []))
            # 复盘先行: 每日复盘(动量量化复盘) → daily_review.json 含 potential_sectors
            # (早期启动推荐的候选池依赖该字段; 幂等: 当日已生成则跳过)
            _review = load_review_data()
            try:
                _hist = {}
                if RPS_HISTORY_FILE.exists():
                    with open(RPS_HISTORY_FILE, encoding="utf-8") as f:
                        _hist = json.load(f)
                _stock_rps50 = calc_stock_rps50_by_industry(dm, ind_df, trade_days)
                _maybe_run_review(build_potential_stats(_hist, _stock_rps50))
            except Exception as _e:
                print(f"  ⚠️ 复盘前置数据构建失败(不阻断): {str(_e)[:80]}")
                _maybe_run_review()
            _review = load_review_data()
            # 复盘驱动选股: 当日调出板块剔除 + daily_review 成熟疲劳/danger 评分扣分
            _out_list = load_out_list_names(_data)
            run_mode = recommend.CONFIG["run_mode"]
            # 五层漏斗: 候选池 100% 来自入选板块成分股 + 板块共振计数
            if run_mode.get("ORIGINAL_MOMENTUM", True):
                rec_list = recommend.recommend_stocks(ind_df, dm, ld, in_list_records,
                                                      out_list_names=_out_list, review_data=_review)
                recommend.save_recommendations(rec_list, ld, pd_)
            if run_mode.get("EARLY_STAGE_START", True):
                rec_early = recommend.recommend_early_stocks(ind_df, dm, ld, in_list_records,
                                                             out_list_names=_out_list, review_data=_review)
                recommend.save_early_recommendations(rec_early, ld, pd_)
            # 周度推荐(中线波段): 每周运行1次, 已生成当周结果则跳过
            _maybe_run_weekly(ind_df, dm, ld, pd_, _data.get("rps20", {}).get("in_list", []),
                              out_list_names=_out_list, review_data=_review)
        except Exception as e:
            print(f"  ⚠️ 推荐生成失败(不阻断): {str(e)[:80]}")
        return
    print(f"  最新交易日: {latest_td}")

    print("\n[2/5] 股票行业...")
    industry_df = get_stock_industry()
    print(f"  {len(industry_df)}只, {industry_df['industry'].nunique()}个行业")

    print("\n[3/5] 日线数据...")
    daily_map = fetch_daily_batch(trade_days)
    print(f"  {len(daily_map)}/{len(trade_days)}天")
    if len(daily_map) < 21:
        write_run_status(False, latest_td, mode="error", message="日线数据不足(<21天)")
        print("❌ 数据不足"); return

    print("\n[4/5] 全量 RPS 历史计算...")
    result = calc_full_rps_history(daily_map, industry_df, trade_days)
    if result is None:
        write_run_status(False, latest_td, mode="error", message="RPS 历史计算失败")
        print("❌ 计算失败"); return
    df_rps, rps_history = result
    print(f"  {len(df_rps)}个行业参与计算")

    # 个股RPS50按行业聚合
    stock_rps50 = calc_stock_rps50_by_industry(daily_map, industry_df, trade_days)
    
    # 行业RPS50近60日≥90天数
    industry_rps50_60day = calc_industry_rps50_60day_count(daily_map, industry_df, trade_days)

    top5 = df_rps.sort_values("RPS5", ascending=False).head(5)
    print(f"  RPS5 TOP5:")
    for _, row in top5.iterrows():
        c = int(row["consecutive5"]) if pd.notna(row.get("consecutive5")) else 0
        d = int(row["days90_5"]) if pd.notna(row.get("days90_5")) else 0
        print(f"    {row['industry']}: RPS5={row.get('RPS5',0):.1f} 连续{c}天 60日直击{d}次")

    print("\n[5/5] 生成看板数据...")
    last_data = {"rps5_names": set(), "rps10_names": set(), "rps20_names": set()}
    if os.path.exists(LAST_RESULT_FILE):
        with open(LAST_RESULT_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            last_data = {
                "rps5_names": set(raw.get("rps5_names", [])),
                "rps10_names": set(raw.get("rps10_names", [])),
                "rps20_names": set(raw.get("rps20_names", [])),
            }

    res5 = split_in_out(df_rps, RPS_THRESHOLD, "RPS5", "rank5", "rank_chg5", "consecutive5", stock_rps50, industry_rps50_60day, last_data["rps5_names"])
    res10 = split_in_out(df_rps, RPS_THRESHOLD, "RPS10", "rank10", "rank_chg10", "consecutive10", stock_rps50, industry_rps50_60day, last_data["rps10_names"])
    res20 = split_in_out(df_rps, RPS_THRESHOLD, "RPS20", "rank20", "rank_chg20", "consecutive20", stock_rps50, industry_rps50_60day, last_data["rps20_names"])

    available_days = [d for d in trade_days if d in daily_map]
    latest_date = available_days[-1]
    prev_date = available_days[-2] if len(available_days) >= 2 else ""

    # ===== 板块成分股（点击弹窗展示）=====
    print("\n  [5b] 构建板块成分股...")
    all_ind_names = set()
    for res in (res5, res10, res20):
        for rec in res["in_list"] + res["out_list"]:
            all_ind_names.add(rec["name"])
    stocks_by_ind = build_sector_stocks(industry_df, daily_map, latest_date, all_ind_names)
    for res in (res5, res10, res20):
        for rec in res["in_list"] + res["out_list"]:
            rec["stocks"] = stocks_by_ind.get(rec["name"], [])

    # 保存历史快照
    with open(RPS_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(rps_history, f, ensure_ascii=False, default=str)

    output = {
        "schema_version": SCHEMA_VERSION,
        "update_date": latest_date,
        "prev_date": prev_date,
        "expected_trade_date": calc_expected_trade_date(trade_days),
        "total_industries": len(df_rps),
        "threshold": RPS_THRESHOLD,
        "rps5": res5,
        "rps10": res10,
        "rps20": res20,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    # 计算成功并完整落盘 → 标记运行成功 (下游 recommend/daily_review/weekly 前置校验)
    write_run_status(True, latest_date, mode="calc", message="RPS 全量计算成功")

    save_last = {
        "date": latest_date,
        "rps5_names": list(res5["current_names"]),
        "rps10_names": list(res10["current_names"]),
        "rps20_names": list(res20["current_names"]),
    }
    with open(LAST_RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(save_last, f, ensure_ascii=False, indent=2)

    # ===== 每日复盘先行(动量视角量化复盘) → daily_review.json 含 potential_sectors
    # (早期启动推荐的候选池依赖该字段; 幂等: 当日已生成则跳过) =====
    try:
        _maybe_run_review(build_potential_stats(rps_history, stock_rps50))
    except Exception as e:
        print(f"  ⚠️ 复盘前置数据构建失败(不阻断): {str(e)[:80]}")
        _maybe_run_review()

    # ===== 每日推荐股票(五层漏斗筛选 + 综合评分, 复用 daily_map/industry_df) =====
    # 失败不阻断主流程, 推荐 JSON 仍输出上次结果或空
    # 复盘联动: 当日调出板块名 + daily_review.json(成熟疲劳/danger 板块 → 选股评分扣分降级)
    _out_list = load_out_list_names(output)
    _review = load_review_data()
    try:
        import recommend
        # 完整入选板块记录(含 name + stocks 成分股), 跨 RPS5/10/20 汇总, 供板块共振计数
        in_list_records = [rec for res in (res5, res10, res20) for rec in res["in_list"]]
        run_mode = recommend.CONFIG["run_mode"]
        if run_mode.get("ORIGINAL_MOMENTUM", True):
            rec_list = recommend.recommend_stocks(industry_df, daily_map, latest_date, in_list_records,
                                                  out_list_names=_out_list, review_data=_review)
            recommend.save_recommendations(rec_list, latest_date, prev_date)
        if run_mode.get("EARLY_STAGE_START", True):
            rec_early = recommend.recommend_early_stocks(industry_df, daily_map, latest_date, in_list_records,
                                                         out_list_names=_out_list, review_data=_review)
            recommend.save_early_recommendations(rec_early, latest_date, prev_date)
    except Exception as e:
        print(f"  ⚠️ 推荐股票生成失败(不阻断主流程): {str(e)[:80]}")

    # ===== 每周推荐股票(中线波段, 周线五层漏斗, 复用 RPS20 入选板块成分股) =====
    _maybe_run_weekly(industry_df, daily_map, latest_date, prev_date, res20["in_list"],
                      out_list_names=_out_list, review_data=_review)

    print(f"\n{'='*55}")
    print(f"  ✅ 完成!")
    print(f"    日期: {latest_date}")
    print(f"    行业: {len(df_rps)}")
    out5 = f"调出{len(res5['out_list'])}个" if res5["out_list"] else ""
    print(f"    RPS5: {len(res5['in_list'])}个入选 {out5}")
    out10 = f"调出{len(res10['out_list'])}个" if res10["out_list"] else ""
    print(f"    RPS10: {len(res10['in_list'])}个入选 {out10}")
    out20 = f"调出{len(res20['out_list'])}个" if res20["out_list"] else ""
    print(f"    RPS20: {len(res20['in_list'])}个入选 {out20}")
    print(f"    文件: {DATA_FILE}")
    print(f"    {'='*55}")

    print("\nToken状态:")
    pool.status()


if __name__ == "__main__":
    main()

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
STATIC_DIR = Path(__file__).parent / "static"
DATA_FILE = STATIC_DIR / "rps_data.json"
LAST_RESULT_FILE = STATIC_DIR / "last_result.json"
RPS_HISTORY_FILE = STATIC_DIR / "rps_history.json"
# ==========================================================


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


def get_stock_industry():
    cache_file = STATIC_DIR / "stock_basic_cache.json"
    if os.path.exists(cache_file):
        df = pd.read_json(cache_file, dtype={"ts_code": str})
        if len(df) > 0:
            return df[["ts_code", "name", "industry"]]
    try:
        df, _ = pool.call("stock_basic")
        df = df[df["industry"].notna() & (df["industry"] != "")]
        result = df[["ts_code", "name", "industry"]]
        result.to_json(cache_file, orient="records", force_ascii=False)
        print(f"  ✅ stock_basic: {len(result)}只, {result['industry'].nunique()}个行业")
        return result
    except Exception as e:
        print(f"  ⚠️ stock_basic全Token受限: {e}")
    print("  ⚠️ 降级: 代码前缀推断板块")
    cache_dir = STATIC_DIR / "daily_cache"
    if cache_dir.exists():
        files = sorted(os.listdir(cache_dir))
        if files:
            df_daily = pd.read_json(cache_dir / files[-1], dtype={"ts_code": str})
            result = df_daily[["ts_code"]].copy()
            result["name"] = result["ts_code"].str.replace(r"\.(SZ|SH|BJ)$", "", regex=True)
            result["industry"] = result["ts_code"].apply(_get_industry_from_code)
            print(f"  ✅ 前缀推断: {result['industry'].nunique()}个分组")
            return result[["ts_code", "name", "industry"]]
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
        # 欧奈尔体系RPS: RPS = (1 - (排名-1)/全部板块总数) * 100，第1名=100
        total_ind = len(df_day_rps) if len(df_day_rps) > 0 else 1
        for period, col in [(5, "RPS5"), (10, "RPS10"), (20, "RPS20")]:
            chg_col = f"chg{period}"
            if chg_col in df_day_rps.columns and df_day_rps[chg_col].notna().sum() > 0:
                # 涨幅从高到低排名，涨幅最高 → 排名1
                df_day_rps[f"rank{period}"] = df_day_rps[chg_col].rank(ascending=False)
                df_day_rps[col] = (1 - (df_day_rps[f"rank{period}"] - 1) / total_ind) * 100

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

    # RPS50 = 百分位排名
    df_stock = pd.DataFrame(list(stock_cum_ret.items()), columns=["ts_code", "chg50"])
    df_stock["RPS50"] = df_stock["chg50"].rank(pct=True, ascending=True) * 100

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

        # RPS50 = 百分位排名
        df_day = pd.DataFrame(list(day_scores.items()), columns=["industry", "chg50"])
        df_day["RPS50"] = df_day["chg50"].rank(pct=True, ascending=True) * 100

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


def main():
    print("=" * 55)
    print("  A股板块 RPS 引擎 v3.0（全量历史回溯）")
    print("=" * 55)

    print("\n[1/5] 交易日历...")
    trade_days = get_trade_days(n=60)
    print(f"  {len(trade_days)} 个交易日")

    print("\n[2/5] 股票行业...")
    industry_df = get_stock_industry()
    print(f"  {len(industry_df)}只, {industry_df['industry'].nunique()}个行业")

    print("\n[3/5] 日线数据...")
    daily_map = fetch_daily_batch(trade_days)
    print(f"  {len(daily_map)}/{len(trade_days)}天")
    if len(daily_map) < 21:
        print("❌ 数据不足"); return

    print("\n[4/5] 全量 RPS 历史计算...")
    result = calc_full_rps_history(daily_map, industry_df, trade_days)
    if result is None:
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
        with open(LAST_RESULT_FILE, "r") as f:
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

    # 保存历史快照
    with open(RPS_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(rps_history, f, ensure_ascii=False, default=str)

    output = {
        "update_date": latest_date,
        "prev_date": prev_date,
        "expected_trade_date": trade_days[-1],
        "total_industries": len(df_rps),
        "threshold": RPS_THRESHOLD,
        "rps5": res5,
        "rps10": res10,
        "rps20": res20,
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    save_last = {
        "rps5_names": list(res5["current_names"]),
        "rps10_names": list(res10["current_names"]),
        "rps20_names": list(res20["current_names"]),
    }
    with open(LAST_RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(save_last, f, ensure_ascii=False, indent=2)

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

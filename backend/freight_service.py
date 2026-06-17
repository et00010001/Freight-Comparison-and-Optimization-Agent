"""运费比价服务 - 支持 CSV 和 SQLite 双数据源，多维度评分和转运路径搜索"""
import os
import time
import logging
import pandas as pd
from typing import List, Optional, Dict, Any
from models import (OrderRequest, CarrierPlan, ComparisonResult, Recommendation,
                    ScoringWeights, TransferPlan, LegPlan)
from graph_router import GraphRouter, RoutingResult, TransferRoute
from base_data_store import BaseDataStore
from common.data_cleaner import clean_dataframe

logger = logging.getLogger(__name__)


# ======================================================================
# CSVDataStore (legacy, @deprecated)
# ======================================================================

class CSVDataStore(BaseDataStore):
    """@deprecated: CSV文件数据源 - 保留作为降级备用"""

    def __init__(self, csv_path: str, extended_csv_path: str = None):
        self.df = pd.read_csv(csv_path)
        self.df.columns = self.df.columns.str.strip()

        if extended_csv_path and os.path.exists(extended_csv_path):
            extended_df = pd.read_csv(extended_csv_path)
            extended_df.columns = extended_df.columns.str.strip()
            self.df = pd.concat([self.df, extended_df], ignore_index=True)
            self.df = self.df.drop_duplicates()
            print(f"已加载扩展数据，总记录数: {len(self.df)}")

        self.df = clean_dataframe(self.df)

        # 检测是否包含真实的服务评分（不全为默认值 'C'）
        if 'Service_Rating' in self.df.columns:
            unique_vals = set(self.df['Service_Rating'].dropna().unique()) - {'C'}
            self.has_service_rating = len(unique_vals) > 0
        else:
            self.has_service_rating = False

    def get_available_ports(self) -> dict:
        orig_ports = sorted(self.df['Orig_Port'].unique().tolist())
        dest_ports = sorted(self.df['Dest_Port'].unique().tolist())
        return {"orig_ports": orig_ports, "dest_ports": dest_ports}

    def count_matching(self, weight: float, orig_port: str, dest_port: str) -> int:
        mask = (
            (self.df['Orig_Port'] == orig_port) &
            (self.df['Dest_Port'] == dest_port) &
            (self.df['Min_Weight_Quant'] <= weight) &
            (self.df['Max_Weight_Quant'] >= weight)
        )
        return int(mask.sum())

    def get_statistics(self) -> dict:
        return {
            "total_records": len(self.df),
            "carriers": sorted(self.df['Carrier'].unique().tolist()),
            "orig_ports": sorted(self.df['Orig_Port'].unique().tolist()),
            "dest_ports": sorted(self.df['Dest_Port'].unique().tolist()),
            "transport_modes": sorted(self.df['Mode_DSC'].unique().tolist()),
            "service_levels": sorted(self.df['Service_Level'].unique().tolist()),
            "has_service_rating": self.has_service_rating,
        }

    def match_plans(self, order: OrderRequest) -> List[Dict[str, Any]]:
        df = self.df
        route_exists = ((df['Orig_Port'] == order.orig_port) & (df['Dest_Port'] == order.dest_port)).any()
        if not route_exists:
            return []

        exact_mask = (
            (df['Orig_Port'] == order.orig_port) &
            (df['Dest_Port'] == order.dest_port) &
            (df['Min_Weight_Quant'] <= order.weight) &
            (df['Max_Weight_Quant'] >= order.weight)
        )
        exact_matched = df[exact_mask]

        if not exact_matched.empty:
            results = []
            for _, row in exact_matched.iterrows():
                results.append({
                    "carrier": row['Carrier'],
                    "orig_port": row['Orig_Port'],
                    "dest_port": row['Dest_Port'],
                    "min_weight": row['Min_Weight_Quant'],
                    "max_weight": row['Max_Weight_Quant'],
                    "service_level": row['Service_Level'],
                    "min_cost": row['Min_Cost'],
                    "rate": row['Rate'],
                    "mode": row['Mode_DSC'],
                    "transport_days": int(row['TPT_Day_Count']),
                    "carrier_type": row['Carrier_Type'],
                    "service_rating": row.get('Service_Rating', 'C'),
                    "is_exact_match": True
                })
            return results

        route_mask = (
            (df['Orig_Port'] == order.orig_port) &
            (df['Dest_Port'] == order.dest_port)
        )
        route_df = df[route_mask]
        if route_df.empty:
            return []

        max_weight = route_df['Max_Weight_Quant'].max()
        max_weight_plans = route_df[route_df['Max_Weight_Quant'] == max_weight]

        if len(max_weight_plans) > 1:
            best_plan = max_weight_plans.loc[max_weight_plans['Rate'].idxmin()]
            results = [{
                "carrier": best_plan['Carrier'],
                "orig_port": best_plan['Orig_Port'],
                "dest_port": best_plan['Dest_Port'],
                "min_weight": best_plan['Min_Weight_Quant'],
                "max_weight": best_plan['Max_Weight_Quant'],
                "service_level": best_plan['Service_Level'],
                "min_cost": best_plan['Min_Cost'],
                "rate": best_plan['Rate'],
                "mode": best_plan['Mode_DSC'],
                "transport_days": int(best_plan['TPT_Day_Count']),
                "carrier_type": best_plan['Carrier_Type'],
                "service_rating": best_plan.get('Service_Rating', 'C'),
                "is_exact_match": False
            }]
        else:
            row = max_weight_plans.iloc[0]
            results = [{
                "carrier": row['Carrier'],
                "orig_port": row['Orig_Port'],
                "dest_port": row['Dest_Port'],
                "min_weight": row['Min_Weight_Quant'],
                "max_weight": row['Max_Weight_Quant'],
                "service_level": row['Service_Level'],
                "min_cost": row['Min_Cost'],
                "rate": row['Rate'],
                "mode": row['Mode_DSC'],
                "transport_days": int(row['TPT_Day_Count']),
                "carrier_type": row['Carrier_Type'],
                "service_rating": row.get('Service_Rating', 'C'),
                "is_exact_match": False
            }]
        return results


# ======================================================================
# DBDataStore (SQLite)
# ======================================================================

class DBDataStore(BaseDataStore):
    """SQLite 数据源 - 通过 SQLAlchemy 查询 freight_rates 表"""

    def __init__(self, db_session_factory=None, auto_init: bool = True):
        if db_session_factory is None:
            from database import get_session_factory
            db_session_factory = get_session_factory()
        self._session_factory = db_session_factory

        self._df = None  # 惰性加载的 DataFrame，供 GraphRouter 使用
        self.has_service_rating = False

        if auto_init:
            self._ensure_data()
            self._detect_service_rating()

        # TTL 缓存
        self._cache: Dict[str, tuple] = {}  # key -> (value, expire_time)
        self._cache_ttl = 300  # 5 分钟

    def _ensure_data(self):
        """确保数据库有数据，否则从 CSV 导入"""
        from sqlalchemy import func
        from db_models import FreightRate
        session = self._session_factory()
        try:
            count = session.query(func.count(FreightRate.id)).scalar()
            if count == 0:
                logger.info("数据库为空，从 CSV 导入...")
                from database import init_db_if_needed
                init_db_if_needed(force=False)
        finally:
            session.close()

    def _detect_service_rating(self):
        """检测数据库中是否包含真实的服务评分（不全为默认值 'C'）"""
        from sqlalchemy import distinct
        from db_models import FreightRate
        session = self._session()
        try:
            ratings = [r[0] for r in session.query(distinct(FreightRate.service_rating)).all()]
            unique_vals = set(ratings) - {'C', None}
            self.has_service_rating = len(unique_vals) > 0
        except Exception:
            self.has_service_rating = False
        finally:
            session.close()

    def refresh_service_rating(self):
        """刷新服务评分检测（上传新数据后调用）"""
        self._df = None  # 清除 DataFrame 缓存
        self._cache.clear()  # 清除 TTL 缓存
        self._detect_service_rating()

    @property
    def df(self):
        """惰性加载 DataFrame，供 GraphRouter 使用（兼容 CSVDataStore 接口）"""
        if self._df is None:
            self._load_df()
        return self._df

    def _load_df(self):
        """从数据库加载全量数据到 DataFrame"""
        from db_models import FreightRate
        session = self._session()
        try:
            rows = session.query(FreightRate).all()
            if not rows:
                self._df = pd.DataFrame()
                return
            data = [row.to_dict() for row in rows]
            self._df = pd.DataFrame(data)
            # 统一列名，与 CSVDataStore 保持一致
            self._df.rename(columns={
                'carrier': 'Carrier',
                'orig_port': 'Orig_Port',
                'dest_port': 'Dest_Port',
                'min_weight': 'Min_Weight_Quant',
                'max_weight': 'Max_Weight_Quant',
                'service_level': 'Service_Level',
                'min_cost': 'Min_Cost',
                'rate': 'Rate',
                'mode': 'Mode_DSC',
                'transport_days': 'TPT_Day_Count',
                'carrier_type': 'Carrier_Type',
                'service_rating': 'Service_Rating',
            }, inplace=True)
        finally:
            session.close()

    def _get_cached(self, key: str):
        """获取缓存值，过期返回 None"""
        if key in self._cache:
            value, expire_time = self._cache[key]
            if time.time() < expire_time:
                return value
            del self._cache[key]
        return None

    def _set_cached(self, key: str, value):
        """设置缓存"""
        self._cache[key] = (value, time.time() + self._cache_ttl)

    def clear_cache(self):
        """清除所有缓存"""
        self._cache.clear()

    def _session(self):
        """获取新会话"""
        return self._session_factory()

    def get_available_ports(self) -> dict:
        cached = self._get_cached("ports")
        if cached is not None:
            return cached

        from sqlalchemy import distinct
        from db_models import FreightRate

        session = self._session()
        try:
            orig_ports = sorted([
                r[0] for r in session.query(distinct(FreightRate.orig_port)).all()
            ])
            dest_ports = sorted([
                r[0] for r in session.query(distinct(FreightRate.dest_port)).all()
            ])
            result = {"orig_ports": orig_ports, "dest_ports": dest_ports}
            self._set_cached("ports", result)
            return result
        finally:
            session.close()

    def count_matching(self, weight: float, orig_port: str, dest_port: str) -> int:
        from sqlalchemy import and_
        from db_models import FreightRate

        session = self._session()
        try:
            count = session.query(FreightRate).filter(
                and_(
                    FreightRate.orig_port == orig_port,
                    FreightRate.dest_port == dest_port,
                    FreightRate.min_weight <= weight,
                    FreightRate.max_weight >= weight,
                )
            ).count()
            return count
        finally:
            session.close()

    def get_statistics(self) -> dict:
        cached = self._get_cached("statistics")
        if cached is not None:
            return cached

        from sqlalchemy import func, distinct
        from db_models import FreightRate

        session = self._session()
        try:
            total = session.query(func.count(FreightRate.id)).scalar()
            carriers = sorted([
                r[0] for r in session.query(distinct(FreightRate.carrier)).all()
            ])
            orig_ports = sorted([
                r[0] for r in session.query(distinct(FreightRate.orig_port)).all()
            ])
            dest_ports = sorted([
                r[0] for r in session.query(distinct(FreightRate.dest_port)).all()
            ])
            modes = sorted([
                r[0] for r in session.query(distinct(FreightRate.mode)).all()
            ])
            levels = sorted([
                r[0] for r in session.query(distinct(FreightRate.service_level)).all()
            ])
            result = {
                "total_records": total,
                "carriers": carriers,
                "orig_ports": orig_ports,
                "dest_ports": dest_ports,
                "transport_modes": modes,
                "service_levels": levels,
                "has_service_rating": self.has_service_rating,
            }
            self._set_cached("statistics", result)
            return result
        finally:
            session.close()

    def match_plans(self, order: OrderRequest) -> List[Dict[str, Any]]:
        from sqlalchemy import and_
        from db_models import FreightRate

        session = self._session()
        try:
            # 检查路线是否存在
            route_exists = session.query(FreightRate).filter(
                and_(
                    FreightRate.orig_port == order.orig_port,
                    FreightRate.dest_port == order.dest_port,
                )
            ).first()
            if not route_exists:
                return []

            # 精确匹配: weight BETWEEN min_weight AND max_weight
            exact_rows = session.query(FreightRate).filter(
                and_(
                    FreightRate.orig_port == order.orig_port,
                    FreightRate.dest_port == order.dest_port,
                    FreightRate.min_weight <= order.weight,
                    FreightRate.max_weight >= order.weight,
                )
            ).order_by(FreightRate.id).all()

            if exact_rows:
                return [self._row_to_dict(row, order.weight, is_exact=True) for row in exact_rows]

            # 超重兜底: max_weight DESC, rate ASC LIMIT 1
            fallback_row = session.query(FreightRate).filter(
                and_(
                    FreightRate.orig_port == order.orig_port,
                    FreightRate.dest_port == order.dest_port,
                )
            ).order_by(
                FreightRate.max_weight.desc(),
                FreightRate.rate.asc(),
            ).first()

            if fallback_row:
                return [self._row_to_dict(fallback_row, order.weight, is_exact=False)]

            return []
        finally:
            session.close()

    @staticmethod
    def _row_to_dict(row, weight: float, is_exact: bool) -> dict:
        """将 ORM 对象转为与 CSVDataStore 输出一致的字典"""
        total_cost = max(float(row.min_cost), float(row.rate) * weight)
        return {
            "carrier": row.carrier,
            "orig_port": row.orig_port,
            "dest_port": row.dest_port,
            "min_weight": float(row.min_weight),
            "max_weight": float(row.max_weight),
            "service_level": row.service_level,
            "min_cost": float(row.min_cost),
            "rate": float(row.rate),
            "mode": row.mode,
            "transport_days": int(row.transport_days),
            "carrier_type": row.carrier_type,
            "service_rating": row.service_rating,
            "is_exact_match": is_exact,
        }


# ======================================================================
# FreightService
# ======================================================================

class FreightService:
    """运费比价服务 - 支持高频查询缓存"""

    def __init__(self, data_store: BaseDataStore):
        self.data_store = data_store
        self._cache: Dict[str, ComparisonResult] = {}
        self._cache_max_size = 100
        self._cache_hits = 0
        self._cache_misses = 0

    def _get_cache_key(self, order: OrderRequest) -> str:
        return f"{order.orig_port}:{order.dest_port}:{order.weight}:{order.max_days}:{order.priority}"

    def _get_from_cache(self, order: OrderRequest) -> Optional[ComparisonResult]:
        key = self._get_cache_key(order)
        if key in self._cache:
            self._cache_hits += 1
            return self._cache[key]
        self._cache_misses += 1
        return None

    def _put_to_cache(self, order: OrderRequest, result: ComparisonResult):
        if len(self._cache) >= self._cache_max_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
        key = self._get_cache_key(order)
        self._cache[key] = result

    def clear_compare_cache(self):
        """清除比价结果缓存"""
        self._cache.clear()

    def get_cache_stats(self) -> Dict[str, Any]:
        total = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total * 100) if total > 0 else 0
        return {
            "cache_size": len(self._cache),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": f"{hit_rate:.1f}%"
        }

    def get_available_ports(self) -> dict:
        return self.data_store.get_available_ports()

    def get_statistics(self) -> dict:
        return self.data_store.get_statistics()

    def calculate_cost(self, rate: float, min_cost: float, weight: float) -> float:
        calculated_cost = rate * weight
        return max(min_cost, calculated_cost)

    def calculate_score(self, plan: CarrierPlan, all_plans: List[CarrierPlan],
                       weights: ScoringWeights) -> tuple:
        costs = [p.total_cost for p in all_plans]
        min_cost, max_cost = min(costs), max(costs)
        if max_cost > min_cost:
            cost_score = 1 - (plan.total_cost - min_cost) / (max_cost - min_cost)
        else:
            cost_score = 1.0

        days = [p.transport_days for p in all_plans]
        min_days, max_days = min(days), max(days)
        if max_days > min_days:
            time_score = 1 - (plan.transport_days - min_days) / (max_days - min_days)
        else:
            time_score = 1.0

        rating_map = {'A': 1.0, 'B': 0.8, 'C': 0.6, 'D': 0.4, 'E': 0.2}
        service_score = rating_map.get(plan.service_rating, 0.5)

        total_score = (
            weights.cost_weight * cost_score +
            weights.time_weight * time_score +
            weights.service_weight * service_score
        )

        details = {
            'cost_score': round(cost_score, 3),
            'time_score': round(time_score, 3),
            'service_score': round(service_score, 3),
            'weights': {
                'cost': weights.cost_weight,
                'time': weights.time_weight,
                'service': weights.service_weight
            }
        }
        return round(total_score, 3), details

    def match_plans(self, order: OrderRequest) -> List[CarrierPlan]:
        raw_plans = self.data_store.match_plans(order)
        plans = []
        for row in raw_plans:
            total_cost = self.calculate_cost(row['rate'], row['min_cost'], order.weight)
            plan = CarrierPlan(
                carrier=row['carrier'],
                orig_port=row['orig_port'],
                dest_port=row['dest_port'],
                min_weight=row['min_weight'],
                max_weight=row['max_weight'],
                service_level=row['service_level'],
                min_cost=round(row['min_cost'], 2),
                rate=round(row['rate'], 4),
                mode=row['mode'],
                transport_days=row['transport_days'],
                carrier_type=row['carrier_type'],
                total_cost=round(total_cost, 2),
                cost_formula=f"max({row['min_cost']:.2f}, {row['rate']:.4f} * {order.weight}) = {total_cost:.2f}",
                service_rating=row.get('service_rating', 'C'),
                is_exact_match=row.get('is_exact_match', True)
            )
            plans.append(plan)
        return plans

    def recommend_plan(self, plans: List[CarrierPlan], order_weight: float = 0,
                      max_days: Optional[int] = None,
                      priority: Optional[str] = None,
                      weights: Optional[ScoringWeights] = None) -> Optional[Recommendation]:
        if not plans:
            return None

        is_overweight = any(not p.is_exact_match for p in plans)

        if order_weight > 1000000:
            return None

        filtered_plans = plans
        if max_days is not None:
            filtered_plans = [p for p in plans if p.transport_days <= max_days]

        if not filtered_plans and max_days is not None and plans:
            sorted_by_days = sorted(plans, key=lambda x: x.transport_days)
            best_available = sorted_by_days[0]
            reason = f"【次优推荐】当前时效要求({max_days}天)内无可用方案，最短运输时间为{best_available.transport_days}天。"
            reason += f"建议放宽时效至{best_available.transport_days}天以上。"
            reason += f"推荐方案：承运商{best_available.carrier}，运输时间{best_available.transport_days}天，总成本${best_available.total_cost:.2f}。"
            return Recommendation(plan=best_available, reason=reason, rank=1)

        if is_overweight and filtered_plans:
            max_weight_plan = max(filtered_plans, key=lambda x: x.max_weight)
            max_weight_value = max_weight_plan.max_weight
            reason = f"因重量超标，请考虑是否分批运输。"
            reason += f"您的货物重量超过该路线所有承运商的最大承运范围（{max_weight_value}kg）。"
            reason += f"最接近的方案：承运商{max_weight_plan.carrier}，最大承运重量{max_weight_value}kg。"
            reason += f"推荐方案：承运商{max_weight_plan.carrier}，运输时间{max_weight_plan.transport_days}天，总成本${max_weight_plan.total_cost:.2f}。"
            return Recommendation(plan=max_weight_plan, reason=reason, rank=1)

        if not filtered_plans:
            return None

        if weights is None:
            if priority == "time":
                weights = ScoringWeights(cost_weight=0.3, time_weight=0.5, service_weight=0.2)
            elif priority == "cost":
                weights = ScoringWeights(cost_weight=0.5, time_weight=0.3, service_weight=0.2)
            else:
                weights = ScoringWeights(cost_weight=0.4, time_weight=0.3, service_weight=0.3)

        # 无服务评分时，将 service 权重平分给 cost 和 time
        if not self.data_store.has_service_rating and weights.service_weight > 0:
            half = weights.service_weight / 2
            weights = ScoringWeights(
                cost_weight=weights.cost_weight + half,
                time_weight=weights.time_weight + half,
                service_weight=0.0,
            )

        for plan in filtered_plans:
            score, details = self.calculate_score(plan, filtered_plans, weights)
            plan.score = score
            plan.score_details = details

        sorted_plans = sorted(filtered_plans, key=lambda x: x.score if x.score else 0, reverse=True)
        best_plan = sorted_plans[0]
        reason = self._generate_enhanced_reason(best_plan, plans, filtered_plans, max_days, weights)
        return Recommendation(plan=best_plan, reason=reason, rank=1)

    def _generate_reason(self, best: CarrierPlan, all_plans: List[CarrierPlan],
                         filtered_plans: List[CarrierPlan], max_days: Optional[int], priority: Optional[str] = None) -> str:
        reasons = []
        if priority == "time":
            reasons.append(f"时间最优：{best.transport_days}天，是最快的方案")
            reasons.append(f"成本：${best.total_cost:.2f}")
        else:
            avg_cost = sum(p.total_cost for p in all_plans) / len(all_plans)
            savings = avg_cost - best.total_cost
            savings_pct = (savings / avg_cost) * 100
            if savings > 0:
                reasons.append(f"成本最优：${best.total_cost:.2f}，比平均水平低${savings:.2f}({savings_pct:.1f}%)")

        if max_days:
            reasons.append(f"满足时效要求：{best.transport_days}天 <= {max_days}天")
        else:
            reasons.append(f"预计运输时间：{best.transport_days}天")

        mode_cn = "空运" if best.mode == "AIR" else "陆运"
        reasons.append(f"运输方式：{mode_cn}")

        service_cn = "门到门" if best.service_level == "DTD" else "门到港"
        reasons.append(f"服务级别：{service_cn}")

        if max_days is not None and len(filtered_plans) < len(all_plans):
            reasons.append(f"时效过滤：从{len(all_plans)}个方案中筛选出{len(filtered_plans)}个满足要求")

        return "；".join(reasons)

    def _generate_enhanced_reason(self, best: CarrierPlan, all_plans: List[CarrierPlan],
                                 filtered_plans: List[CarrierPlan], max_days: Optional[int],
                                 weights: ScoringWeights) -> str:
        reasons = []
        if best.score is not None:
            reasons.append(f"综合评分：{best.score:.3f}/1.0（加权算法）")
        if best.score_details:
            details = best.score_details
            reasons.append(f"成本得分：{details['cost_score']:.3f}（权重{weights.cost_weight:.0%}）")
            reasons.append(f"时效得分：{details['time_score']:.3f}（权重{weights.time_weight:.0%}）")
            reasons.append(f"服务得分：{details['service_score']:.3f}（权重{weights.service_weight:.0%}）")
        reasons.append(f"总成本：${best.total_cost:.2f}")
        reasons.append(f"运输时间：{best.transport_days}天")
        reasons.append(f"服务评级：{best.service_rating or '未评级'}")
        mode_cn = "空运" if best.mode == "AIR" else "陆运"
        reasons.append(f"运输方式：{mode_cn}")
        service_cn = "门到门" if best.service_level == "DTD" else "门到港"
        reasons.append(f"服务级别：{service_cn}")
        if max_days is not None and len(filtered_plans) < len(all_plans):
            reasons.append(f"时效过滤：从{len(all_plans)}个方案中筛选出{len(filtered_plans)}个满足要求")
        return "；".join(reasons)

    def _score_transfer_route(self, route: TransferRoute,
                              all_routes: List[TransferRoute],
                              weights: ScoringWeights) -> TransferRoute:
        if not all_routes:
            return route

        costs = [r.total_min_cost for r in all_routes]
        min_c, max_c = min(costs), max(costs)
        cost_score = (1 - (route.total_min_cost - min_c) / (max_c - min_c)
                      if max_c > min_c else 1.0)

        days = [r.total_estimated_days for r in all_routes]
        min_d, max_d = min(days), max(days)
        time_score = (1 - (route.total_estimated_days - min_d) / (max_d - min_d)
                      if max_d > min_d else 1.0)

        rating_map = {'A': 1.0, 'B': 0.8, 'C': 0.6, 'D': 0.4, 'E': 0.2}
        service_score = rating_map.get(route.avg_service_rating, 0.5)

        total_score = round(
            weights.cost_weight * cost_score +
            weights.time_weight * time_score +
            weights.service_weight * service_score, 3
        )

        route.score = total_score
        route.score_details = {
            'cost_score': round(cost_score, 3),
            'time_score': round(time_score, 3),
            'service_score': round(service_score, 3),
            'weights': {
                'cost': weights.cost_weight,
                'time': weights.time_weight,
                'service': weights.service_weight
            }
        }
        return route

    def compare(self, order: OrderRequest) -> ComparisonResult:
        cached_result = self._get_from_cache(order)
        if cached_result:
            return cached_result

        plans = self.match_plans(order)
        has_direct = any(p.is_exact_match for p in plans)

        if order.weights:
            weights = order.weights
        elif order.priority == "time":
            weights = ScoringWeights(cost_weight=0.3, time_weight=0.5, service_weight=0.2)
        elif order.priority == "cost":
            weights = ScoringWeights(cost_weight=0.5, time_weight=0.3, service_weight=0.2)
        else:
            weights = ScoringWeights(cost_weight=0.4, time_weight=0.3, service_weight=0.3)

        exact_plans = [p for p in plans if p.is_exact_match]
        if exact_plans:
            for plan in exact_plans:
                score, details = self.calculate_score(plan, exact_plans, weights)
                plan.score = score
                plan.score_details = details

        # 只在有精确匹配方案时才生成直达推荐，否则交给转运搜索
        recommendation = self.recommend_plan(
            exact_plans, order.weight, order.max_days, order.priority, weights
        ) if exact_plans else None

        transfer_routes = None
        fallback_transfer = None
        fallback_reason = ""

        if not has_direct:
            router = GraphRouter(self.data_store)
            routing_result = router.find_routes(
                order.orig_port, order.dest_port, order.weight, order.max_days
            )

            all_transfer = routing_result.transfer_routes
            if all_transfer:
                for tr in all_transfer:
                    self._score_transfer_route(tr, all_transfer, weights)
                all_transfer.sort(key=lambda r: r.score or 0, reverse=True)
                transfer_routes = self._build_transfer_plans(all_transfer, router)

                if not recommendation:
                    best_transfer = all_transfer[0]
                    # 用最优转运方案的第一段构建推荐
                    best_first_leg = min(best_transfer.legs[0], key=lambda x: x['total_cost'])
                    rec_plan = CarrierPlan(
                        carrier=best_first_leg['carrier'],
                        orig_port=best_first_leg['orig_port'],
                        dest_port=best_first_leg['dest_port'],
                        min_weight=best_first_leg['min_weight'],
                        max_weight=best_first_leg['max_weight'],
                        service_level=best_first_leg['service_level'],
                        min_cost=best_first_leg['min_cost'],
                        rate=best_first_leg['rate'],
                        mode=best_first_leg['mode'],
                        transport_days=best_transfer.total_estimated_days,
                        carrier_type=best_first_leg['carrier_type'],
                        total_cost=best_transfer.total_min_cost,
                        cost_formula=f"转运{best_transfer.hop_count}段合计",
                        service_rating=best_transfer.avg_service_rating,
                        score=best_transfer.score,
                    )
                    reason = (
                        f"转运推荐: 经{best_transfer.hop_count}次中转 "
                        f"{' → '.join(best_transfer.path)}, "
                        f"总成本${best_transfer.total_min_cost:.2f}, "
                        f"预计{best_transfer.total_estimated_days}天"
                    )
                    recommendation = Recommendation(plan=rec_plan, reason=reason, rank=1)

            if not transfer_routes and routing_result.fallback_route:
                fb = routing_result.fallback_route
                fallback_transfer = self._build_single_transfer_plan(fb, router)
                if order.max_days is not None and fb.is_direct:
                    fallback_reason = (
                        f"【次优推荐】当前时效要求({order.max_days}天)内无可用方案，"
                        f"最短运输时间为{fb.total_estimated_days}天。"
                        f"建议放宽时效至{fb.total_estimated_days}天以上。"
                    )
                elif order.max_days is not None:
                    fallback_reason = (
                        f"【次优推荐】当前时效要求({order.max_days}天)内无可用方案，"
                        f"最快转运方案需要{fb.total_estimated_days}天"
                        f"(经{fb.hop_count}次中转)。"
                        f"建议放宽时效要求。"
                    )
                else:
                    fallback_reason = (
                        f"【次优推荐】未找到直达方案，"
                        f"最近转运路径: {' → '.join(fb.path)}，"
                        f"预计{fb.total_estimated_days}天。"
                    )

        result = ComparisonResult(
            order_info=order,
            available_plans=plans,
            recommended_plan=recommendation,
            total_plans_found=len(exact_plans),
            filtered_by_time=order.max_days is not None,
            scoring_weights={
                'cost_weight': weights.cost_weight,
                'time_weight': weights.time_weight,
                'service_weight': weights.service_weight
            },
            has_direct_route=has_direct,
            transfer_routes=transfer_routes,
            fallback_transfer=fallback_transfer,
            fallback_reason=fallback_reason
        )

        self._put_to_cache(order, result)
        return result

    def _build_transfer_plans(self, routes: List[TransferRoute],
                               router: GraphRouter) -> List[TransferPlan]:
        plans = []
        for route in routes:
            tp = self._build_single_transfer_plan(route, router)
            plans.append(tp)
        return plans

    def _build_single_transfer_plan(self, route: TransferRoute,
                                     router: GraphRouter) -> TransferPlan:
        legs = []
        for i, leg_plans in enumerate(route.legs):
            best = min(leg_plans, key=lambda x: x['total_cost'])
            leg = LegPlan(
                from_port=route.path[i],
                to_port=route.path[i + 1],
                carrier=best['carrier'],
                mode=best['mode'],
                service_level=best['service_level'],
                transport_days=best['transport_days'],
                total_cost=best['total_cost'],
                cost_formula=best['cost_formula'],
                service_rating=best.get('service_rating', 'C')
            )
            legs.append(leg)

        leg_details = []
        for j, leg in enumerate(legs):
            mode_cn = "空运" if leg.mode == "AIR" else "陆运"
            svc_cn = "门到门" if leg.service_level == "DTD" else "门到港"
            leg_details.append(
                f"第{j+1}段 {leg.from_port}→{leg.to_port}: "
                f"{leg.carrier} {mode_cn}({svc_cn}) "
                f"${leg.total_cost:.2f} / {leg.transport_days}天"
            )

        score_info = ""
        if route.score is not None:
            score_info = f", 综合评分{route.score:.3f}"

        reason_parts = [
            f"转运方案 (经{route.hop_count}次中转): {' → '.join(route.path)}",
            f"总成本: ${route.total_min_cost:.2f}{score_info}",
            f"总耗时: {route.total_estimated_days}天 "
            f"(运输{route.total_min_days}天 + 转运{route.hop_count * route.transfer_penalty_days}天)"
        ]

        return TransferPlan(
            path=route.path,
            legs=legs,
            total_cost=round(route.total_min_cost, 2),
            total_days=route.total_min_days,
            hop_count=route.hop_count,
            transfer_penalty_days=route.transfer_penalty_days,
            total_estimated_days=route.total_estimated_days,
            is_direct=False,
            route_display=router.format_route_display(route),
            leg_details=leg_details,
            recommendation_reason="; ".join(reason_parts),
            score=route.score,
            avg_service_rating=route.avg_service_rating
        )

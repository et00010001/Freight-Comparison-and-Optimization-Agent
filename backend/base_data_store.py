"""数据源抽象基类 - 定义统一的数据访问接口

CSVDataStore 和 DBDataStore 均实现此接口，确保可互换使用。
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any


class BaseDataStore(ABC):
    """数据源抽象基类"""

    has_service_rating: bool = False

    @abstractmethod
    def get_available_ports(self) -> dict:
        """获取可用港口列表，返回 {"orig_ports": [...], "dest_ports": [...]}"""
        ...

    @abstractmethod
    def get_statistics(self) -> dict:
        """获取数据统计信息"""
        ...

    @abstractmethod
    def count_matching(self, weight: float, orig_port: str, dest_port: str) -> int:
        """统计匹配的记录数"""
        ...

    @abstractmethod
    def match_plans(self, order) -> List[Dict[str, Any]]:
        """匹配承运商方案，返回 (list[dict], bool) 或 list[dict]"""
        ...

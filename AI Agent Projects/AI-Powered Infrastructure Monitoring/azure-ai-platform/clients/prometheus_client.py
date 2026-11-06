# Prometheus API Client
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class PrometheusClient:
    """Client for querying Prometheus metrics"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {}
        if api_key:
            self.headers['Authorization'] = f'Bearer {api_key}'
    
    async def query(self, query: str) -> Dict[str, Any]:
        """Execute instant query"""
        url = f"{self.base_url}/api/v1/query"
        params = {"query": query}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
    
    async def query_range(
        self, 
        query: str, 
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        step: str = "15s"
    ) -> Dict[str, Any]:
        """Execute range query"""
        if not start:
            start = datetime.utcnow() - timedelta(hours=1)
        if not end:
            end = datetime.utcnow()
        
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
    
    async def get_cpu_usage(self, instance: str) -> float:
        """Get current CPU usage percentage"""
        query = f'100 - (avg by (instance) (irate(node_cpu_seconds_total{{mode="idle", instance="{instance}"}}[5m])) * 100)'
        result = await self.query(query)
        
        if result['data']['result']:
            return float(result['data']['result'][0]['value'][1])
        return 0.0
    
    async def get_memory_usage(self, instance: str) -> float:
        """Get current memory usage percentage"""
        query = f'(1 - (node_memory_MemAvailable_bytes{{instance="{instance}"}} / node_memory_MemTotal_bytes{{instance="{instance}"}})) * 100'
        result = await self.query(query)
        
        if result['data']['result']:
            return float(result['data']['result'][0]['value'][1])
        return 0.0
    
    async def get_disk_usage(self, instance: str, mountpoint: str = "/") -> float:
        """Get disk usage percentage"""
        query = f'(1 - (node_filesystem_avail_bytes{{instance="{instance}", mountpoint="{mountpoint}"}} / node_filesystem_size_bytes{{instance="{instance}", mountpoint="{mountpoint}"}})) * 100'
        result = await self.query(query)
        
        if result['data']['result']:
            return float(result['data']['result'][0]['value'][1])
        return 0.0
    
    async def get_api_latency_p95(self, instance: str, endpoint: Optional[str] = None) -> float:
        """Get P95 API latency"""
        if endpoint:
            query = f'histogram_quantile(0.95, rate(api_request_duration_seconds_bucket{{instance="{instance}", endpoint="{endpoint}"}}[5m]))'
        else:
            query = f'histogram_quantile(0.95, rate(api_request_duration_seconds_bucket{{instance="{instance}"}}[5m]))'
        
        result = await self.query(query)
        
        if result['data']['result']:
            return float(result['data']['result'][0]['value'][1])
        return 0.0
    
    async def get_database_connections(self, instance: str) -> Dict[str, Any]:
        """Get database connection metrics"""
        active_query = f'pg_stat_database_numbackends{{instance="{instance}"}}'
        max_query = f'pg_settings_max_connections{{instance="{instance}"}}'
        
        active_result = await self.query(active_query)
        max_result = await self.query(max_query)
        
        active = 0
        max_conn = 100
        
        if active_result['data']['result']:
            active = int(active_result['data']['result'][0]['value'][1])
        
        if max_result['data']['result']:
            max_conn = int(max_result['data']['result'][0]['value'][1])
        
        return {
            "active_connections": active,
            "max_connections": max_conn,
            "utilization": active / max_conn if max_conn > 0 else 0
        }
    
    async def get_error_rate(self, instance: str, time_range: str = "5m") -> float:
        """Get 5xx error rate"""
        query = f'(rate(api_http_5xx_total{{instance="{instance}"}}[{time_range}]) / rate(api_requests_total{{instance="{instance}"}}[{time_range}])) * 100'
        result = await self.query(query)
        
        if result['data']['result']:
            return float(result['data']['result'][0]['value'][1])
        return 0.0

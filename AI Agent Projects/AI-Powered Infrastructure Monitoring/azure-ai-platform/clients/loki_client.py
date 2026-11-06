# Loki API Client
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class LokiClient:
    """Client for querying Loki logs"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {}
        if api_key:
            self.headers['Authorization'] = f'Bearer {api_key}'
    
    async def query_range(
        self,
        query: str,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 1000
    ) -> Dict[str, Any]:
        """Execute LogQL range query"""
        if not start:
            start = datetime.utcnow() - timedelta(hours=1)
        if not end:
            end = datetime.utcnow()
        
        url = f"{self.base_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": int(start.timestamp() * 1e9),  # nanoseconds
            "end": int(end.timestamp() * 1e9),
            "limit": limit
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
    
    async def get_error_logs(
        self,
        instance: str,
        time_range_minutes: int = 30
    ) -> List[Dict[str, Any]]:
        """Get error logs for an instance"""
        start = datetime.utcnow() - timedelta(minutes=time_range_minutes)
        query = f'{{instance="{instance}", job="application"}} |= "error" | json'
        
        result = await self.query_range(query, start=start)
        
        logs = []
        if result.get('data', {}).get('result'):
            for stream in result['data']['result']:
                for entry in stream.get('values', []):
                    logs.append({
                        "timestamp": entry[0],
                        "message": entry[1],
                        "labels": stream.get('stream', {})
                    })
        
        return logs
    
    async def get_exception_patterns(
        self,
        instance: str,
        time_range_minutes: int = 30
    ) -> Dict[str, int]:
        """Get exception patterns and counts"""
        start = datetime.utcnow() - timedelta(minutes=time_range_minutes)
        
        # Query for different exception types
        exception_types = [
            "DatabaseError",
            "ConnectionError",
            "TimeoutError",
            "OperationalError",
            "PoolError"
        ]
        
        patterns = {}
        
        for exc_type in exception_types:
            query = f'count_over_time({{instance="{instance}", job="application"}} |= "{exc_type}" [30m])'
            try:
                result = await self.query_range(query, start=start)
                if result.get('data', {}).get('result'):
                    count = sum(int(v[1]) for stream in result['data']['result'] for v in stream.get('values', []))
                    if count > 0:
                        patterns[exc_type] = count
            except Exception as e:
                logger.error(f"Error querying pattern {exc_type}: {e}")
        
        return patterns
    
    async def get_log_samples(
        self,
        instance: str,
        level: str = "error",
        limit: int = 10
    ) -> List[str]:
        """Get sample log messages"""
        start = datetime.utcnow() - timedelta(minutes=30)
        query = f'{{instance="{instance}", job="application", level="{level}"}}'
        
        result = await self.query_range(query, start=start, limit=limit)
        
        samples = []
        if result.get('data', {}).get('result'):
            for stream in result['data']['result']:
                for entry in stream.get('values', [])[:limit]:
                    samples.append(entry[1])
        
        return samples

# Tempo API Client
import httpx
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class TempoClient:
    """Client for querying Tempo traces"""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {}
        if api_key:
            self.headers['Authorization'] = f'Bearer {api_key}'
    
    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """Get trace by ID"""
        url = f"{self.base_url}/api/traces/{trace_id}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
    
    async def search_traces(
        self,
        service_name: Optional[str] = None,
        operation_name: Optional[str] = None,
        limit: int = 20
    ) -> Dict[str, Any]:
        """Search traces"""
        url = f"{self.base_url}/api/search"
        params = {"limit": limit}
        
        if service_name:
            params['service.name'] = service_name
        if operation_name:
            params['name'] = operation_name
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=self.headers, timeout=30.0)
            response.raise_for_status()
            return response.json()

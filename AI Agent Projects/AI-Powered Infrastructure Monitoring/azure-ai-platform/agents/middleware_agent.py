# Middleware Agent - Redis, WebSocket, Background Workers
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class MiddlewareAgent:
    """Analyzes optional middleware components (Redis, WebSocket, Workers)"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "middleware"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze middleware component health"""
        logger.info(f"Middleware Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        components = {}
        enabled_components = []
        
        try:
            # Check Redis
            redis_status = await self._check_redis(instance)
            components["redis"] = redis_status
            if redis_status["enabled"]:
                enabled_components.append("redis")
                if redis_status.get("anomalies"):
                    anomalies.extend(redis_status["anomalies"])
            
            # Check WebSocket (placeholder - would check websocket_connections_active)
            websocket_status = await self._check_websocket(instance)
            components["websocket"] = websocket_status
            if websocket_status["enabled"]:
                enabled_components.append("websocket")
            
            # Check Background Workers (placeholder)
            worker_status = await self._check_workers(instance)
            components["workers"] = worker_status
            if worker_status["enabled"]:
                enabled_components.append("workers")
            
            metrics = {
                "components": components,
                "enabled": enabled_components,
                "total_enabled": len(enabled_components)
            }
            
            # Determine overall status
            if any(c.get("status") == "critical" for c in components.values() if c.get("enabled")):
                status = "critical"
            elif anomalies:
                status = "degraded"
            elif enabled_components:
                status = "healthy"
            else:
                status = "healthy"  # No optional components enabled
            
            result = AgentOutput(
                agent_name=self.name,
                status=status,
                metrics=metrics,
                anomalies=anomalies,
                confidence="high"
            )
            
            state.middleware_status = result.dict()
            state.execution_steps.append(f"Middleware Agent completed: {status}, enabled: {enabled_components}")
            
            return result
        
        except Exception as e:
            logger.error(f"Middleware Agent error: {e}")
            state.errors.append(f"Middleware Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )
    
    async def _check_redis(self, instance: str) -> Dict[str, Any]:
        """Check Redis availability and health"""
        try:
            # Check if Redis exporter is up
            redis_up_query = f'redis_up{{instance="{instance}"}}'
            result = await self.prometheus.query(redis_up_query)
            
            if not result['data']['result']:
                return {"enabled": False, "status": "not_installed"}
            
            redis_up = int(float(result['data']['result'][0]['value'][1]))
            
            if redis_up == 0:
                return {
                    "enabled": True,
                    "status": "critical",
                    "anomalies": [{
                        "type": "redis_down",
                        "severity": "critical",
                        "details": "Redis is not responding"
                    }]
                }
            
            # Check memory usage
            memory_query = f'redis_memory_used_bytes{{instance="{instance}"}}'
            memory_result = await self.prometheus.query(memory_query)
            
            memory_pct = 0
            if memory_result['data']['result']:
                memory_used = float(memory_result['data']['result'][0]['value'][1])
                # Assume 256MB max (from docker-compose)
                memory_pct = (memory_used / (256 * 1024 * 1024)) * 100
            
            anomalies = []
            if memory_pct > 90:
                anomalies.append({
                    "type": "high_redis_memory",
                    "severity": "warning",
                    "details": f"Redis memory usage at {memory_pct:.1f}%"
                })
            
            return {
                "enabled": True,
                "status": "healthy" if not anomalies else "degraded",
                "memory_usage_pct": memory_pct,
                "anomalies": anomalies
            }
        
        except Exception as e:
            logger.debug(f"Redis check failed (likely not enabled): {e}")
            return {"enabled": False, "status": "not_installed"}
    
    async def _check_websocket(self, instance: str) -> Dict[str, Any]:
        """Check WebSocket health (placeholder)"""
        try:
            ws_query = f'websocket_connections_active{{instance="{instance}"}}'
            result = await self.prometheus.query(ws_query)
            
            if not result['data']['result']:
                return {"enabled": False, "status": "not_installed"}
            
            return {"enabled": True, "status": "healthy"}
        
        except Exception:
            return {"enabled": False, "status": "not_installed"}
    
    async def _check_workers(self, instance: str) -> Dict[str, Any]:
        """Check background workers (placeholder)"""
        try:
            worker_query = f'background_worker_up{{instance="{instance}"}}'
            result = await self.prometheus.query(worker_query)
            
            if not result['data']['result']:
                return {"enabled": False, "status": "not_installed"}
            
            return {"enabled": True, "status": "healthy"}
        
        except Exception:
            return {"enabled": False, "status": "not_installed"}

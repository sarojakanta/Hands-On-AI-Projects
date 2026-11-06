# Nginx Monitoring Agent
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class NginxMonitoringAgent:
    """Analyzes Nginx web server health and upstream performance"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "nginx_monitoring"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze Nginx web server health"""
        logger.info(f"Nginx Monitoring Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get active connections
            conn_query = f'nginx_connections_active{{instance="{instance}"}}'
            conn_result = await self.prometheus.query(conn_query)
            active_connections = 0
            if conn_result['data']['result']:
                active_connections = int(float(conn_result['data']['result'][0]['value'][1]))
            
            # Get request rate
            req_query = f'rate(nginx_http_requests_total{{instance="{instance}"}}[5m])'
            req_result = await self.prometheus.query(req_query)
            request_rate = 0
            if req_result['data']['result']:
                request_rate = float(req_result['data']['result'][0]['value'][1])
            
            # Get 5xx error rate
            error_5xx_query = f'rate(nginx_http_response_code_total{{instance="{instance}", code=~"5.."}}[5m])'
            error_result = await self.prometheus.query(error_5xx_query)
            error_rate_5xx = 0
            if error_result['data']['result']:
                error_rate_5xx = float(error_result['data']['result'][0]['value'][1])
            
            # Calculate error percentage
            error_pct = 0
            if request_rate > 0:
                error_pct = (error_rate_5xx / request_rate) * 100
            
            metrics = {
                "active_connections": active_connections,
                "request_rate_per_sec": request_rate,
                "error_rate_5xx_per_sec": error_rate_5xx,
                "error_percentage": error_pct
            }
            
            # Detect anomalies
            if active_connections > 1000:
                anomalies.append({
                    "type": "high_connections",
                    "severity": "warning",
                    "details": f"{active_connections} active connections"
                })
            
            if error_pct > 5:
                anomalies.append({
                    "type": "high_5xx_rate",
                    "severity": "critical",
                    "details": f"5xx error rate at {error_pct:.1f}%"
                })
            elif error_pct > 2:
                anomalies.append({
                    "type": "elevated_5xx_rate",
                    "severity": "warning",
                    "details": f"5xx error rate at {error_pct:.1f}%"
                })
            
            # Determine status
            if any(a['severity'] == 'critical' for a in anomalies):
                status = "critical"
            elif anomalies:
                status = "degraded"
            else:
                status = "healthy"
            
            result = AgentOutput(
                agent_name=self.name,
                status=status,
                metrics=metrics,
                anomalies=anomalies,
                confidence="high"
            )
            
            state.nginx_status = result.dict()
            state.execution_steps.append(f"Nginx Monitoring Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"Nginx Monitoring Agent error: {e}")
            state.errors.append(f"Nginx Monitoring Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

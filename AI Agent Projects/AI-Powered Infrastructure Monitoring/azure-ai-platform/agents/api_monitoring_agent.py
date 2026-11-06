# REST API Monitoring Agent
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class APIMonitoringAgent:
    """Analyzes REST API performance and traffic patterns"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "rest_api_monitoring"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze API performance"""
        logger.info(f"API Monitoring Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get P95 latency
            p95_latency = await self.prometheus.get_api_latency_p95(instance)
            
            # Get error rate
            error_rate = await self.prometheus.get_error_rate(instance)
            
            # Get request rate
            rps_query = f'rate(api_requests_total{{instance="{instance}"}}[5m])'
            rps_result = await self.prometheus.query(rps_query)
            rps = 0
            if rps_result['data']['result']:
                rps = float(rps_result['data']['result'][0]['value'][1])
            
            # Get concurrent requests
            concurrent_query = f'api_requests_in_progress{{instance="{instance}"}}'
            concurrent_result = await self.prometheus.query(concurrent_query)
            concurrent = 0
            if concurrent_result['data']['result']:
                concurrent = int(float(concurrent_result['data']['result'][0]['value'][1]))
            
            metrics = {
                "p95_latency_seconds": p95_latency,
                "error_rate_pct": error_rate,
                "requests_per_second": rps,
                "concurrent_requests": concurrent
            }
            
            # Detect anomalies
            if p95_latency > 5.0:
                anomalies.append({
                    "type": "critical_api_latency",
                    "severity": "critical",
                    "details": f"P95 latency {p95_latency:.2f}s (baseline: <1s)"
                })
            elif p95_latency > 2.0:
                anomalies.append({
                    "type": "high_api_latency",
                    "severity": "warning",
                    "details": f"P95 latency {p95_latency:.2f}s"
                })
            
            if error_rate > 5:
                anomalies.append({
                    "type": "high_error_rate",
                    "severity": "warning",
                    "details": f"Error rate {error_rate:.1f}%"
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
            
            state.api_metrics = result.dict()
            state.execution_steps.append(f"API Monitoring Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"API Monitoring Agent error: {e}")
            state.errors.append(f"API Monitoring Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

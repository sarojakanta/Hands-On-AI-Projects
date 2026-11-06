# Application Health Agent
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class ApplicationHealthAgent:
    """Analyzes application process health and worker status"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "app_health"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze application health"""
        logger.info(f"Application Health Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get process status
            process_query = f'app_process_up{{instance="{instance}"}}'
            process_result = await self.prometheus.query(process_query)
            process_up = 0
            if process_result['data']['result']:
                process_up = int(float(process_result['data']['result'][0]['value'][1]))
            
            # Get worker metrics
            worker_total_query = f'app_worker_total{{instance="{instance}"}}'
            worker_total_result = await self.prometheus.query(worker_total_query)
            worker_total = 8  # default
            if worker_total_result['data']['result']:
                worker_total = int(float(worker_total_result['data']['result'][0]['value'][1]))
            
            worker_busy_query = f'app_worker_busy{{instance="{instance}"}}'
            worker_busy_result = await self.prometheus.query(worker_busy_query)
            worker_busy = 0
            if worker_busy_result['data']['result']:
                worker_busy = int(float(worker_busy_result['data']['result'][0]['value'][1]))
            
            # Get exception rate
            exception_query = f'rate(app_exceptions_total{{instance="{instance}"}}[5m])'
            exception_result = await self.prometheus.query(exception_query)
            exception_rate = 0
            if exception_result['data']['result']:
                exception_rate = float(exception_result['data']['result'][0]['value'][1])
            
            # Calculate worker utilization
            worker_utilization = 0
            if worker_total > 0:
                worker_utilization = worker_busy / worker_total
            
            metrics = {
                "process_up": process_up,
                "worker_total": worker_total,
                "worker_busy": worker_busy,
                "worker_utilization": worker_utilization,
                "exception_rate_per_sec": exception_rate
            }
            
            # Detect anomalies
            if process_up == 0:
                anomalies.append({
                    "type": "process_down",
                    "severity": "critical",
                    "details": "Application process is not running"
                })
            
            if worker_utilization > 0.95:
                anomalies.append({
                    "type": "worker_saturation",
                    "severity": "critical",
                    "details": f"Worker utilization at {worker_utilization*100:.1f}% - nearing saturation"
                })
            elif worker_utilization > 0.85:
                anomalies.append({
                    "type": "high_worker_utilization",
                    "severity": "warning",
                    "details": f"Worker utilization at {worker_utilization*100:.1f}%"
                })
            
            if exception_rate > 10:
                anomalies.append({
                    "type": "high_exception_rate",
                    "severity": "warning",
                    "details": f"Exception rate: {exception_rate:.1f}/sec"
                })
            
            # Determine status
            if process_up == 0 or any(a['severity'] == 'critical' for a in anomalies):
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
            
            state.app_health = result.dict()
            state.execution_steps.append(f"Application Health Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"Application Health Agent error: {e}")
            state.errors.append(f"Application Health Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

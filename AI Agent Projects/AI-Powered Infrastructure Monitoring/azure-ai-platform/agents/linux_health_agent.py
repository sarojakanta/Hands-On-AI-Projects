# Linux Health Agent - Analyzes OS-level resources
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class LinuxHealthAgent:
    """Analyzes Linux system resources (CPU, memory, disk, network)"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "linux_health"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze Linux infrastructure health"""
        logger.info(f"Linux Health Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get CPU usage
            cpu_usage = await self.prometheus.get_cpu_usage(instance)
            
            # Get memory usage
            memory_usage = await self.prometheus.get_memory_usage(instance)
            
            # Get disk usage
            disk_usage = await self.prometheus.get_disk_usage(instance)
            
            metrics = {
                "cpu_utilization": cpu_usage / 100,
                "memory_usage_pct": memory_usage / 100,
                "disk_usage_pct": disk_usage / 100
            }
            
            # Detect anomalies
            if cpu_usage > 90:
                anomalies.append({
                    "type": "critical_cpu_usage",
                    "severity": "critical",
                    "details": f"CPU usage at {cpu_usage:.1f}%"
                })
            elif cpu_usage > 80:
                anomalies.append({
                    "type": "high_cpu_usage",
                    "severity": "warning",
                    "details": f"CPU usage at {cpu_usage:.1f}%"
                })
            
            if memory_usage > 85:
                anomalies.append({
                    "type": "high_memory_usage",
                    "severity": "warning",
                    "details": f"Memory usage at {memory_usage:.1f}%"
                })
            
            if disk_usage > 85:
                anomalies.append({
                    "type": "low_disk_space",
                    "severity": "warning",
                    "details": f"Disk usage at {disk_usage:.1f}%"
                })
            
            # Determine overall status
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
            
            state.linux_health = result.dict()
            state.execution_steps.append(f"Linux Health Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"Linux Health Agent error: {e}")
            state.errors.append(f"Linux Health Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

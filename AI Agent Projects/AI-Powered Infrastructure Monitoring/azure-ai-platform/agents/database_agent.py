# Database Monitoring Agent
from typing import Dict, Any
from clients.prometheus_client import PrometheusClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class DatabaseMonitoringAgent:
    """Analyzes PostgreSQL health, connections, and query performance"""
    
    def __init__(self, prometheus_client: PrometheusClient):
        self.name = "database_monitoring"
        self.prometheus = prometheus_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze database health"""
        logger.info(f"Database Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get connection metrics
            conn_metrics = await self.prometheus.get_database_connections(instance)
            
            # Get slow query rate
            slow_query_query = f'rate(pg_slow_queries_total{{instance="{instance}"}}[5m])'
            slow_query_result = await self.prometheus.query(slow_query_query)
            slow_queries = 0
            if slow_query_result['data']['result']:
                slow_queries = float(slow_query_result['data']['result'][0]['value'][1])
            
            metrics = {
                "active_connections": conn_metrics['active_connections'],
                "max_connections": conn_metrics['max_connections'],
                "connection_utilization": conn_metrics['utilization'],
                "slow_queries_per_sec": slow_queries
            }
            
            # Detect anomalies
            if conn_metrics['utilization'] > 0.95:
                anomalies.append({
                    "type": "critical_connection_pool_saturation",
                    "severity": "critical",
                    "details": f"{conn_metrics['active_connections']}/{conn_metrics['max_connections']} connections in use",
                    "impact": "New connections may be rejected"
                })
            elif conn_metrics['utilization'] > 0.80:
                anomalies.append({
                    "type": "high_connection_utilization",
                    "severity": "warning",
                    "details": f"{conn_metrics['active_connections']}/{conn_metrics['max_connections']} connections in use"
                })
            
            if slow_queries > 5:
                anomalies.append({
                    "type": "slow_query_increase",
                    "severity": "warning",
                    "details": f"Slow query rate: {slow_queries:.2f}/sec"
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
            
            state.database_health = result.dict()
            state.execution_steps.append(f"Database Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"Database Agent error: {e}")
            state.errors.append(f"Database Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

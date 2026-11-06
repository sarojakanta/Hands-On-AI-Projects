# Log Analysis Agent
from typing import Dict, Any
from clients.loki_client import LokiClient
from models.schemas import AgentState, AgentOutput
import logging

logger = logging.getLogger(__name__)


class LogAnalysisAgent:
    """Analyzes application logs for error patterns and exceptions"""
    
    def __init__(self, loki_client: LokiClient):
        self.name = "log_analysis"
        self.loki = loki_client
    
    async def execute(self, state: AgentState) -> AgentOutput:
        """Analyze logs for errors and patterns"""
        logger.info(f"Log Analysis Agent analyzing {state.affected_machine}")
        
        instance = state.affected_machine
        anomalies = []
        
        try:
            # Get exception patterns
            patterns = await self.loki.get_exception_patterns(instance, time_range_minutes=30)
            
            # Get error log samples
            error_samples = await self.loki.get_log_samples(instance, level="error", limit=10)
            
            metrics = {
                "exception_patterns": patterns,
                "total_errors_30min": sum(patterns.values()),
                "unique_error_types": len(patterns),
                "sample_count": len(error_samples)
            }
            
            # Detect anomalies
            if "PoolError" in patterns or "OperationalError" in patterns:
                anomalies.append({
                    "type": "connection_pool_exhaustion_logs",
                    "severity": "critical",
                    "details": f"Database connection errors detected: {patterns.get('PoolError', 0) + patterns.get('OperationalError', 0)} occurrences",
                    "correlation": "Matches database connection saturation metric"
                })
            
            if "TimeoutError" in patterns and patterns["TimeoutError"] > 10:
                anomalies.append({
                    "type": "timeout_errors",
                    "severity": "warning",
                    "details": f"Timeout errors: {patterns['TimeoutError']} occurrences"
                })
            
            total_errors = sum(patterns.values())
            if total_errors > 50:
                anomalies.append({
                    "type": "high_error_volume",
                    "severity": "warning",
                    "details": f"Total errors in last 30min: {total_errors}"
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
            
            # Add log samples to result
            result.metrics['log_samples'] = error_samples[:5]
            
            state.log_analysis = result.dict()
            state.execution_steps.append(f"Log Analysis Agent completed: {status}")
            
            return result
        
        except Exception as e:
            logger.error(f"Log Analysis Agent error: {e}")
            state.errors.append(f"Log Analysis Agent: {str(e)}")
            return AgentOutput(
                agent_name=self.name,
                status="unknown",
                confidence="low"
            )

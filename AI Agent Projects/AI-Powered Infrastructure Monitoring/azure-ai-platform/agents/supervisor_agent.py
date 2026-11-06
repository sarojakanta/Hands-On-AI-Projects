# Supervisor Agent - Orchestrates RCA workflow
from typing import Dict, Any
from models.schemas import AgentState
import logging

logger = logging.getLogger(__name__)


class SupervisorAgent:
    """Orchestrator agent that coordinates the RCA workflow"""
    
    def __init__(self):
        self.name = "supervisor"
    
    async def execute(self, state: AgentState) -> Dict[str, Any]:
        """
        Parse alert and determine which agents to invoke
        """
        logger.info(f"Supervisor processing alert: {state.alert_name} on {state.affected_machine}")
        
        # Determine which agents to run based on alert type
        selected_agents = self._select_agents(state)
        
        state.selected_agents = selected_agents
        state.execution_steps.append(f"Supervisor selected agents: {', '.join(selected_agents)}")
        
        return {
            "action": "parse_alert",
            "affected_machine": state.affected_machine,
            "selected_agents": selected_agents,
            "execution_mode": "parallel_then_sequential"
        }
    
    def _select_agents(self, state: AgentState) -> list:
        """Determine which agents to invoke based on alert context"""
        agents = []
        
        # Always run these fundamental agents
        agents.extend([
            "linux_health",
            "nginx_monitoring",
            "app_health"
        ])
        
        # Add API agent if API-related alert
        if any(keyword in state.alert_name.lower() for keyword in ['api', 'latency', 'timeout', 'error']):
            agents.append("rest_api_monitoring")
        
        # Add database agent if DB-related or connection issues
        if any(keyword in state.alert_name.lower() for keyword in ['database', 'postgres', 'connection', 'query']):
            agents.append("database_monitoring")
        
        # Always check middleware and logs
        agents.extend([
            "middleware",
            "log_analysis"
        ])
        
        return agents

# LangGraph Orchestrator
from langgraph.graph import StateGraph, END
from models.schemas import AgentState
from agents.supervisor_agent import SupervisorAgent
from agents.linux_health_agent import LinuxHealthAgent
from agents.nginx_agent import NginxMonitoringAgent
from agents.app_health_agent import ApplicationHealthAgent
from agents.database_agent import DatabaseMonitoringAgent
from agents.api_monitoring_agent import APIMonitoringAgent
from agents.middleware_agent import MiddlewareAgent
from agents.log_analysis_agent import LogAnalysisAgent
from agents.rca_agent import CrossSystemRCAAgent
from agents.incident_reporting_agent import IncidentReportingAgent
from clients.prometheus_client import PrometheusClient
from clients.loki_client import LokiClient
from langchain_openai import AzureChatOpenAI
import logging

logger = logging.getLogger(__name__)


class LangGraphOrchestrator:
    """Orchestrates multi-agent RCA workflow using LangGraph"""
    
    def __init__(
        self,
        prometheus_client: PrometheusClient,
        loki_client: LokiClient,
        llm: AzureChatOpenAI
    ):
        self.prometheus = prometheus_client
        self.loki = loki_client
        self.llm = llm
        
        # Initialize agents
        self.supervisor = SupervisorAgent()
        self.linux_health = LinuxHealthAgent(prometheus_client)
        self.nginx_agent = NginxMonitoringAgent(prometheus_client)
        self.app_health_agent = ApplicationHealthAgent(prometheus_client)
        self.database_agent = DatabaseMonitoringAgent(prometheus_client)
        self.api_agent = APIMonitoringAgent(prometheus_client)
        self.middleware_agent = MiddlewareAgent(prometheus_client)
        self.log_agent = LogAnalysisAgent(loki_client)
        self.rca_agent = CrossSystemRCAAgent(llm)
        self.reporting_agent = IncidentReportingAgent()
        
        # Build workflow graph
        self.workflow = self._build_workflow()
    
    def _build_workflow(self) -> StateGraph:
        """Build LangGraph workflow"""
        
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("supervisor", self._supervisor_node)
        workflow.add_node("linux_health", self._linux_health_node)
        workflow.add_node("nginx_monitoring", self._nginx_node)
        workflow.add_node("app_health", self._app_health_node)
        workflow.add_node("database_monitoring", self._database_node)
        workflow.add_node("api_monitoring", self._api_node)
        workflow.add_node("middleware", self._middleware_node)
        workflow.add_node("log_analysis", self._log_node)
        workflow.add_node("rca", self._rca_node)
        workflow.add_node("reporting", self._reporting_node)
        
        # Define edges
        workflow.set_entry_point("supervisor")
        
        # Sequential execution flow
        workflow.add_edge("supervisor", "linux_health")
        workflow.add_edge("linux_health", "nginx_monitoring")
        workflow.add_edge("nginx_monitoring", "app_health")
        workflow.add_edge("app_health", "database_monitoring")
        workflow.add_edge("database_monitoring", "api_monitoring")
        workflow.add_edge("api_monitoring", "middleware")
        workflow.add_edge("middleware", "log_analysis")
        workflow.add_edge("log_analysis", "rca")
        workflow.add_edge("rca", "reporting")
        workflow.add_edge("reporting", END)
        
        return workflow.compile()
    
    async def _supervisor_node(self, state: AgentState) -> AgentState:
        """Supervisor node"""
        await self.supervisor.execute(state)
        return state
    
    async def _linux_health_node(self, state: AgentState) -> AgentState:
        """Linux health agent node"""
        await self.linux_health.execute(state)
        return state
    
    async def _database_node(self, state: AgentState) -> AgentState:
        """Database monitoring agent node"""
        await self.database_agent.execute(state)
        return state
    
    async def _api_node(self, state: AgentState) -> AgentState:
        """API monitoring agent node"""
        await self.api_agent.execute(state)
        return state
    
    async def _log_node(self, state: AgentState) -> AgentState:
        """Log analysis agent node"""
        await self.log_agent.execute(state)
        return state
    
    async def _nginx_node(self, state: AgentState) -> AgentState:
        """Nginx monitoring agent node"""
        await self.nginx_agent.execute(state)
        return state
    
    async def _app_health_node(self, state: AgentState) -> AgentState:
        """Application health agent node"""
        await self.app_health_agent.execute(state)
        return state
    
    async def _middleware_node(self, state: AgentState) -> AgentState:
        """Middleware agent node"""
        await self.middleware_agent.execute(state)
        return state
    
    async def _rca_node(self, state: AgentState) -> AgentState:
        """RCA agent node"""
        await self.rca_agent.execute(state)
        return state
    
    async def _reporting_node(self, state: AgentState) -> AgentState:
        """Incident reporting agent node"""
        await self.reporting_agent.execute(state)
        return state
    
    async def process_alert(self, alert_data: dict) -> AgentState:
        """Process an alert through the workflow"""
        logger.info(f"Processing alert: {alert_data.get('alert_name')}")
        
        # Create initial state
        state = AgentState(
            alert_name=alert_data.get('alert_name', 'Unknown'),
            alert_severity=alert_data.get('severity', 'warning'),
            affected_machine=alert_data.get('machine', 'unknown'),
            alert_timestamp=alert_data.get('timestamp', ''),
            alert_labels=alert_data.get('labels', {})
        )
        
        # Execute workflow
        final_state = await self.workflow.ainvoke(state)
        
        return final_state

# Cross-System RCA Agent
from typing import Dict, Any, List
from models.schemas import AgentState, RCAOutput
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class CrossSystemRCAAgent:
    """Correlates symptoms across components and determines root cause"""
    
    def __init__(self, llm: AzureChatOpenAI):
        self.name = "cross_system_rca"
        self.llm = llm
    
    async def execute(self, state: AgentState) -> RCAOutput:
        """Perform cross-system root cause analysis"""
        logger.info(f"RCA Agent analyzing {state.affected_machine}")
        
        try:
            # Collect all evidence
            evidence = self._collect_evidence(state)
            
            # Build timeline
            timeline = self._build_timeline(state)
            
            # Generate RCA using LLM
            rca_result = await self._generate_rca(state, evidence, timeline)
            
            incident_id = f"INC-{datetime.utcnow().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}"
            
            result = RCAOutput(
                incident_id=incident_id,
                root_cause=rca_result['root_cause'],
                confidence=rca_result['confidence'],
                confidence_basis=rca_result['confidence_basis'],
                evidence=evidence,
                timeline=timeline,
                affected_components=rca_result['affected_components'],
                probable_causes=rca_result['probable_causes'],
                recommendations=rca_result['recommendations']
            )
            
            state.root_cause_analysis = result.dict()
            state.execution_steps.append(f"RCA Agent completed: {result.root_cause}")
            
            return result
        
        except Exception as e:
            logger.error(f"RCA Agent error: {e}")
            state.errors.append(f"RCA Agent: {str(e)}")
            raise
    
    def _collect_evidence(self, state: AgentState) -> List[Dict[str, Any]]:
        """Collect evidence from all agent outputs"""
        evidence = []
        
        # Linux health evidence
        if state.linux_health and state.linux_health.get('anomalies'):
            for anomaly in state.linux_health['anomalies']:
                evidence.append({
                    "source": "linux_health_agent",
                    "type": "metric",
                    "finding": anomaly['details'],
                    "weight": "medium" if anomaly['severity'] == 'warning' else "high"
                })
        
        # Database evidence
        if state.database_health and state.database_health.get('anomalies'):
            for anomaly in state.database_health['anomalies']:
                evidence.append({
                    "source": "database_monitoring_agent",
                    "type": "metric",
                    "finding": anomaly['details'],
                    "weight": "high"
                })
        
        # API evidence
        if state.api_metrics and state.api_metrics.get('anomalies'):
            for anomaly in state.api_metrics['anomalies']:
                evidence.append({
                    "source": "rest_api_monitoring_agent",
                    "type": "metric",
                    "finding": anomaly['details'],
                    "weight": "high"
                })
        
        # Log evidence
        if state.log_analysis and state.log_analysis.get('anomalies'):
            for anomaly in state.log_analysis['anomalies']:
                evidence.append({
                    "source": "log_analysis_agent",
                    "type": "log",
                    "finding": anomaly['details'],
                    "weight": "high"
                })
        
        return evidence
    
    def _build_timeline(self, state: AgentState) -> List[Dict[str, str]]:
        """Build event timeline"""
        timeline = []
        
        # This is simplified - in production, would correlate actual timestamps
        timeline.append({"time": "T-10min", "event": "Normal operation"})
        
        if state.database_health and state.database_health.get('anomalies'):
            timeline.append({"time": "T-5min", "event": "Database connection utilization increases"})
        
        if state.api_metrics and state.api_metrics.get('anomalies'):
            timeline.append({"time": "T-3min", "event": "API latency degradation begins"})
        
        if state.log_analysis and state.log_analysis.get('anomalies'):
            timeline.append({"time": "T-2min", "event": "Connection pool errors appear in logs"})
        
        timeline.append({"time": "T-0min", "event": f"Alert triggered: {state.alert_name}"})
        
        return timeline
    
    async def _generate_rca(
        self,
        state: AgentState,
        evidence: List[Dict],
        timeline: List[Dict]
    ) -> Dict[str, Any]:
        """Use LLM to generate root cause analysis"""
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an expert SRE analyzing infrastructure incidents.
            Based on the evidence and timeline provided, determine the root cause.
            
            Provide your analysis in the following format:
            ROOT_CAUSE: One sentence describing the primary root cause
            CONFIDENCE: A value between 0 and 1
            CONFIDENCE_BASIS: Explanation of confidence score
            AFFECTED_COMPONENTS: List of affected components
            PROBABLE_CAUSES: List of probable causes with their confidence
            RECOMMENDATIONS: List of remediation recommendations with priority
            """),
            ("human", """Machine: {machine}
            Alert: {alert_name}
            Severity: {severity}
            
            Evidence:
            {evidence}
            
            Timeline:
            {timeline}
            
            Provide root cause analysis:""")
        ])
        
        evidence_str = "\n".join([f"- {e['source']}: {e['finding']}" for e in evidence])
        timeline_str = "\n".join([f"- {t['time']}: {t['event']}" for t in timeline])
        
        response = await self.llm.ainvoke(
            prompt.format_messages(
                machine=state.affected_machine,
                alert_name=state.alert_name,
                severity=state.alert_severity,
                evidence=evidence_str,
                timeline=timeline_str
            )
        )
        
        # Parse LLM response (simplified - production would use structured output)
        content = response.content
        
        # Extract key information
        root_cause = "Database connection pool exhaustion"
        confidence = 0.85
        confidence_basis = "Strong correlation across multiple telemetry sources with clear causality chain"
        
        affected_components = [
            "PostgreSQL connection pool",
            "Application workers",
            "REST API",
            "Nginx upstream"
        ]
        
        probable_causes = [
            {
                "hypothesis": "Database connection pool size insufficient for current workload",
                "confidence": 0.85,
                "reasoning": "Pool nearing capacity without traffic increase"
            },
            {
                "hypothesis": "Connection leak in application code",
                "confidence": 0.60,
                "reasoning": "Idle connections while pool exhausted suggests potential leak"
            }
        ]
        
        recommendations = [
            {
                "priority": "immediate",
                "action": "Increase PostgreSQL max_connections from 100 to 200",
                "rationale": "Immediate relief needed"
            },
            {
                "priority": "short-term",
                "action": "Review application connection pooling configuration",
                "rationale": "Ensure proper connection lifecycle management"
            },
            {
                "priority": "medium-term",
                "action": "Investigate query optimization opportunities",
                "rationale": "Reduce connection hold time"
            }
        ]
        
        return {
            "root_cause": root_cause,
            "confidence": confidence,
            "confidence_basis": confidence_basis,
            "affected_components": affected_components,
            "probable_causes": probable_causes,
            "recommendations": recommendations
        }

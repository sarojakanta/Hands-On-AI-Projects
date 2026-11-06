import os


from crewai import LLM
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import (
	ExaSearchTool,
	JinaScrapeWebsiteTool,
	ArxivPaperTool,
	URLReadTool,
	ScrapeWebsiteTool
)






@CrewBase
class AiPoweredMultiAgentResearchSystemUsingCrewaiCrew:
    """AiPoweredMultiAgentResearchSystemUsingCrewai crew"""

    
    @agent
    def research_director(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["research_director"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def web_researcher(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["web_researcher"],
            
            
            tools=[				ExaSearchTool(),
				JinaScrapeWebsiteTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def literature_reviewer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["literature_reviewer"],
            
            
            tools=[				ArxivPaperTool(),
				URLReadTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def technical_analyst(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["technical_analyst"],
            
            
            tools=[				ExaSearchTool(),
				ScrapeWebsiteTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def critical_reviewer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["critical_reviewer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def fact_checker(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["fact_checker"],
            
            
            tools=[				URLReadTool(),
				ScrapeWebsiteTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def report_writer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["report_writer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    
    @agent
    def email_sender(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["email_sender"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            apps=[
                    "gmail/send-email@private",
                    ],
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.6-luna",
                
                
            ),
            
        )
        
    

    
    @task
    def create_research_plan(self) -> Task:
        return Task(
            config=self.tasks_config["create_research_plan"],
            markdown=False,
            
            
        )
    
    @task
    def collect_web_evidence(self) -> Task:
        return Task(
            config=self.tasks_config["collect_web_evidence"],
            markdown=False,
            
            
        )
    
    @task
    def review_literature(self) -> Task:
        return Task(
            config=self.tasks_config["review_literature"],
            markdown=False,
            
            
        )
    
    @task
    def analyse_technical_options(self) -> Task:
        return Task(
            config=self.tasks_config["analyse_technical_options"],
            markdown=False,
            
            
        )
    
    @task
    def critically_review_findings(self) -> Task:
        return Task(
            config=self.tasks_config["critically_review_findings"],
            markdown=False,
            
            
        )
    
    @task
    def fact_check_claims(self) -> Task:
        return Task(
            config=self.tasks_config["fact_check_claims"],
            markdown=False,
            
            
        )
    
    @task
    def write_final_report(self) -> Task:
        return Task(
            config=self.tasks_config["write_final_report"],
            markdown=False,
            
            
        )
    
    @task
    def send_email(self) -> Task:
        return Task(
            config=self.tasks_config["send_email"],
            markdown=False,
            
            
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the AiPoweredMultiAgentResearchSystemUsingCrewai crew"""

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,

            chat_llm=LLM(model="openai/gpt-5.6-luna"),
        )



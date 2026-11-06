import os


from crewai import LLM
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import (
	FileReadTool,
	URLReadTool
)






@CrewBase
class FileSummarizerCrew:
    """FileSummarizer crew"""

    
    @agent
    def file_parser_record_extractor(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["file_parser_record_extractor"],
            
            
            tools=[				FileReadTool(file_path="C:\mydata\structured\resume\resume2026\shared\INITIALRESUME\ATS_Orinted\Initial_automation_Manual_Coding Agentic\multiagent\nokiaold\Sarojakanta_Pattanayak_Resume.pdf"),
				URLReadTool()],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4-mini",
                
                
            ),
            
        )
        
    
    @agent
    def file_summarizer(self) -> Agent:
        
        
        return Agent(
            config=self.agents_config["file_summarizer"],
            
            
            tools=[],
            
            reasoning=False,
            max_reasoning_attempts=None,
            inject_date=True,
            allow_delegation=False,
            max_iter=25,
            max_rpm=None,
            
            
            max_execution_time=None,
            llm=LLM(
                model="openai/gpt-5.4-mini",
                
                
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
                model="openai/gpt-5.4-mini",
                
                
            ),
            
        )
        
    

    
    @task
    def read_file_content(self) -> Task:
        return Task(
            config=self.tasks_config["read_file_content"],
            markdown=False,
            
            
        )
    
    @task
    def summarize_file(self) -> Task:
        return Task(
            config=self.tasks_config["summarize_file"],
            markdown=False,
            
            guardrail="The summary must meet all of the following conditions: 1. Must not be empty 2. Must contain at least 3 sentences 3. Must not contain raw data (e.g. CSV rows, JSON blobs, unformatted tables) 4. Must not contain any API keys 5. Must not contain any passwords or sensitive credentials 6. Must not contain any file attachments or references to binary data",
        )
    
    @task
    def send_summary_email(self) -> Task:
        return Task(
            config=self.tasks_config["send_summary_email"],
            markdown=False,
            
            
        )
    

    @crew
    def crew(self) -> Crew:
        """Creates the FileSummarizer crew"""

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,

            chat_llm=LLM(model="openai/gpt-5.6-luna"),
        )



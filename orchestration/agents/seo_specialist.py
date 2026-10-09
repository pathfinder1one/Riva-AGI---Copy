import logging
import time
from orchestration import InputData, AgentResponse, ResponseStatus
from orchestration.orchestrator.infra import registry, AgentCapabilities, key_manager, call_gemini

logger = logging.getLogger(__name__)

@registry.register("seo_specialist", AgentCapabilities(description="Optimizes content for search engines.", tools=["optimize_seo"], agent_level="TASK_DOER"))
def seo_specialist_agent(task_data: InputData) -> AgentResponse:
    logger.info("Routing to SEO Specialist Agent")
    start_time = time.time()
    
    my_key = key_manager.get_api_key_for_role("SEO_SPECIALIST")
    # System instruction tailored for this agent
    sys_prompt = "You are the SEO Specialist Agent. Your mission is to optimize digital content, metadata, headings, keyword relevance, and search engine discoverability."
    
    content = f"### SEO Specialist Output\\nGenerated template for {task_data.text_content[:20]}..."
    execution_time = (time.time() - start_time) * 1000
    
    return AgentResponse(
        agent_id="seo_specialist",
        status=ResponseStatus.SUCCESS,
        content=content,
        tool_calls=[],
        execution_time_ms=execution_time,
        metadata={"processed_modality": task_data.input_type.value}
    )

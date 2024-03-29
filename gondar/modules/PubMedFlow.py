"""
Document Structuring Workflow Based on PubMed Data

---------------------------------

The scripts should be included following models:\n

1. Tools: The Interface of External API or Function,
such as entrez (Network access interface for external resources)
and Aliyun function computation. \n

2. Document: The data model coming from Tools.

3. PromptTemplate: The string template that use for organize the prompt.

4. Messages: The data model sending to Agent (LLM).

5. Agent: LLM interface.

6. Parser: The data (processing) model that parsering the coming data from Agent (LLM).

7. Callback: Various plug-and-play callback models,
such as the callback model for recording Tokens Usage
or the callback model for interacting with the frontend.

8. Memory: A model for interfacing with databases,
which may include but is not limited to Memory, Redis, or databases like Postgre, Mongo, etc.
Used to provide the model with cached, persistent memory, or historical records.

---------------------------------

Combining the above models into a workflow (Flow) model.
This Flow model will directly provide interfaces for external access.

"""

from typing import Any, Dict, Generator, List, Literal

from pydantic import BaseModel, model_validator

from gondar import Gconfig
from gondar.utils.types import POS_FLOAT, POS_INT, STR, VALID


class GondarModel(BaseModel):
    class Config:
        arbitrary_types_allowed = True


class Message(GondarModel):
    role: str
    content: str


class AzureOpenAIWrapper(GondarModel):
    """
    Azure OpenAI API Document: \n
    https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/json-mode?tabs=python
    """

    client: Any = None

    azure_openai_endpoint: STR
    azure_deployment: STR
    azure_openai_key: STR
    azure_api_version: STR = "2023-12-01-preview"

    model: Literal[
        "gpt-4-1106-preview", "gpt-35-turbo-1106"
    ] = "gpt-4-1106-preview"  # Since 12/07/2023
    response_format: Dict = {"type": "json_object"}
    temperature: POS_FLOAT = 0.0

    max_retries: POS_INT = 2  # times
    timeout: POS_INT = 300  # seconds

    @model_validator(mode="before")
    @classmethod
    def validate_import(cls, values: Dict) -> Dict:
        try:
            from openai import AzureOpenAI
        except ImportError as error:
            raise error("Failed to import AzureOpenAI from openai packages.")

        return values

    @model_validator(mode="after")
    def validate_client(self) -> VALID:
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=self.azure_openai_endpoint,
            azure_deployment=self.azure_deployment,
            api_version=self.azure_api_version,
            api_key=self.azure_openai_key,
        )

    def invoke(self, messages: List[Message]) -> Dict:
        return self.client.chat.completions.create(
            messages=messages,
            model=self.model,
            response_format=self.response_format,
            temperature=self.temperature,
            seed=1001,
        )


class MessageTemplate(GondarModel):
    role: str
    template: str

    def fill(self, **kwargs):
        return Message(role=self.role, content=self.template.format(**kwargs))


class MessagesWrapper(GondarModel):
    template_store: List[MessageTemplate]

    def generate(
        self, reference: str, heads: List[str], motivation: str
    ) -> List[Message]:
        heads = str(heads)

        _messages: Generator[Message] = (
            template.fill(reference=reference, heads=heads, motivation=motivation)
            for template in self.template_store
        )

        return [mes for mes in _messages]


if __name__ == "__main__":
    import json

    import polars as pl

    from gondar.tools import EntrezAPIWrapper

    # NOTE: Model Tools (Interface Model of External API or Function)
    entrez = EntrezAPIWrapper(retmax=3)

    # NOTE: Model Document (Retrieved External Document Model)
    data = entrez.load("(Chlamydomonas reinhardtii) AND (Terpene)")

    # NOTE: Model PromptTemplate (The Template Model of Prompt)
    # Each Requirement is fucking crucial. Don't even move a char.
    helpful_assistant: Dict[str, str] = {
        "role": "system",
        "template": """
        You are an intelligent research assistant.

        Your thought process:
        * Understand the user's motivation.
        * Self-ask: Is there sufficient content reported in reference text?
        * Self-ask: Does the content type match the header type (within the brackets)?"
        * Find reference text that satisfy all headers and then organize corresponding rows from these reference paragraphs.
        * Organize the row one by one into a concise, tidy structured data list.
        
        Present the list as JSON object:
        {{ 
            headers: [header1, header2, ...],
            satisfy: [Yes/No, Yes/No, ...],
            data: {{row1: [column1, column2, ...], row2: [column1, column2, ...], ...}}
        }}
        
        Requirements:
        * You pay thorough attention to the entire reference text.
        * Your output content should be sourced directly from the provided reference text without any modifications.
        * You evaluate whether the content of the reference text satisfies all headers. If it doesn't meet any header, you return an empty list.
        * You cannot return any content that could be defined as 'Not specified'.
        * You should explode the list to prevent too much content in the same row.
        * You must ensure consistent column count for each row.
        * You output content without '\\n'.
        """,
    }

    text_extract: Dict[str, str] = {
        "role": "user",
        "template": """
        Motivation:
        {motivation}
        
        You should find reference paragraphs that meet my motivation from the following all reference text:
        {reference}
        
        You should find content that satisfies the following all headers:
        {heads}
        
        Take a deep breath, remember all your tasks, and all Requirements.
        Print JSON object:
        """,
    }

    self_check: Dict[str, str] = {
        "role": "assistant",
        "template": """     
        Let me carefully check if the reference text contains the content required by the headers {heads}.
        If not satisfied, then satisfy is 'No'; if satisfied, satisfy will be 'Yes'.
        
        I will finish all my tasks with all your requirement. Here is the prefectest list print as JSON:
        """,
    }

    templates: List[MessageTemplate] = [
        MessageTemplate(**helpful_assistant),
        MessageTemplate(**text_extract),
        MessageTemplate(**self_check),
    ]

    # NOTE: Model Messages (The Messages Model, adapting to the Agent Model)
    messagesWrapper = MessagesWrapper(template_store=templates)

    heads = [
        "Engineered strains (Named Entity)",
        "Terpenes Types (Named Entity)",
        "Terpenes Production (Values / Unit)",
        "Protocol (Brief)",
    ]

    motivation = """
    Retrieve engineered strain of Chlamydomonas reinhardtii and the production of any types of terpenes.
    """

    # NOTE: Model Agent (The Agent model, an interface of LLM)
    llm = AzureOpenAIWrapper(
        azure_openai_endpoint=Gconfig.AZURE_OPENAI_ENDPOINT,
        azure_deployment=Gconfig.AZURE_DEPLOYMENT,
        azure_openai_key=Gconfig.AZURE_OPENAI_KEY,
        model="gpt-4-1106-preview",
    )

    print(data[0]["article"])
    for body in data[0]["body"]:
        mes = messagesWrapper.generate(
            reference=body,
            heads=heads,
            motivation=motivation,
        )

        # NOTE: Model Parser (The output parser that parsering the responese from LLM to constructured format)
        res = llm.invoke(mes)
        print(res.usage)
        c = res.choices[0].message.content.strip()
        cd = json.loads(c)
        print(c)
        if cd["data"] != {}:
            df = pl.DataFrame(data=cd["data"]).transpose()
            df = df.rename(dict(zip(df.columns, cd["headers"])))
            print(df)

            print("\n")

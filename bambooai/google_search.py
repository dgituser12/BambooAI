import openai
import re
import json
import numpy as np
import requests
import os
from newspaper import Article, Config
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

openai_client = openai.OpenAI()

MAX_ITERATIONS = 5
CHUNK_SIZE = 512
TOP_K_RESULTS = 6
SEARCH_RESULTS = 5
NUM_DOCUMENTS = 30

class ChatBot:
    def __init__(self):
        self.agent = 'Google Search Query Generator'
        self.completion = None
        
    def __call__(self, log_and_call_manager, chain_id, messages):
        result = self.execute(log_and_call_manager, chain_id, messages)
        return result

    def execute(self,log_and_call_manager, chain_id, messages):
        try:
            # Attempt package-relative import
            from . import models
        except ImportError:
            # Fall back to script-style import
            import models

        self.completion = models.llm_stream(log_and_call_manager,messages, agent=self.agent, chain_id=chain_id)

        return self.completion

class SmartSearchOrchestrator:
    action_re = re.compile(r'^Action: (\w+): (.*)$')

    def __init__(self, log_and_call_manager=None,chain_id=None, messages=None):
        self.log_and_call_manager = log_and_call_manager
        self.chain_id = chain_id
        self.messages = messages
     
        self.chat_bot = ChatBot() 
        self.google_search = Search()
        self.calculate = Calculator()

        self.known_actions = {
            "google_search": self.google_search,
            "calculate": self.calculate,
}

    def perform_query(self, log_and_call_manager, chain_id, messages, max_turns=MAX_ITERATIONS):
        try:
            # Attempt package-relative import
            from . import output_manager
        except ImportError:
            # Fall back to script-style import
            import output_manager

        output_handler = output_manager.OutputManager()

        i = 0
        observation = None
        next_prompt = messages
        while i < max_turns:
            i += 1
            result = self.chat_bot(log_and_call_manager, chain_id, next_prompt)
            next_prompt.append({"role": "assistant", "content": result})
            actions = [self.action_re.match(a) for a in result.split('\n') if self.action_re.match(a)]
            if actions:
                # There is an action to run
                action, action_input = actions[0].groups()
                if action not in self.known_actions:
                    raise Exception("Unknown action: {}: {}".format(action, action_input))
                output_handler.display_search_task(action, action_input)
                if os.environ.get('SELENIUM_WEBDRIVER_PATH'):
                    output_handler.display_system_messages(f"Using the Selenium WebDriver at: {os.environ.get('SELENIUM_WEBDRIVER_PATH')}")
                observation, links = self.known_actions[action](log_and_call_manager, chain_id, action_input)
                if links:
                    for link in links:
                        output_handler.print_wrapper(f"\nTitle: {link['title']}\nLink: {link['link']}")
                #output_handler.print_wrapper("\nObservation:", observation)
                next_prompt.append({"role": "user", "content": "Observation: {}".format(observation)})
            else:
                break

        return result
    
    def __call__(self, log_and_call_manager, chain_id, messages):
        return self.perform_query(log_and_call_manager, chain_id, messages)
    
### SEARCH ACTIONS ###

# Define a class to perform a Google search and retrieve the content of the resulting pages    
class SearchEngine:
    def __init__(self):
        webdriver_path = os.environ.get('SELENIUM_WEBDRIVER_PATH')
        if webdriver_path and webdriver_path.strip():
            self.webdriver_path = os.path.normpath(webdriver_path)
        else:
            self.webdriver_path = None
        self.driver = None
        self.headless = True
        
        if self.webdriver_path:
            # Initialize Selenium WebDriver if path is provided
            service = ChromeService(executable_path=self.webdriver_path)
            options = Options()
            if self.headless:
                options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            # Set up logging preferences to suppress console messages
            options.add_argument("--log-level=3")  # Suppress console logs

            # Optionally, you can set the logging level for the browser specifically
            options.set_capability('goog:loggingPrefs', {'browser': 'OFF', 'driver': 'OFF', 'performance': 'OFF', 'server': 'OFF'})

            self.driver = webdriver.Chrome(service=service, options=options)

    def __del__(self):
        # Quit the WebDriver when the instance is destroyed, if it was initialized
        if self.driver:
            self.driver.quit()

    # Perform a Google search using the SERPer API
    def search_google(self, query, gl='us', hl='en'):
        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": query, "gl": gl, "hl": hl, "num": SEARCH_RESULTS, "autocorrect": True})
        headers = {'X-API-KEY': os.environ['SERPER_API_KEY'], 'Content-Type': 'application/json'}

        response = requests.request("POST", url, headers=headers, data=payload)
        response = json.loads(response.text)

        return response
    
    # Download and parse an article from a URL using the Newspaper library
    def search_url(self, url, document_size=CHUNK_SIZE):
        try:
            if self.driver:
                # Use Selenium to get the dynamic content
                self.driver.get(url)
                full_html = self.driver.page_source
            else:
                # Use Newspaper3 to get the static HTML content
                full_html = None

            # Use Newspaper3 to parse the HTML content
            config = Config()
            config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            config.memoize_articles = False  # Disable caching
            article = Article(url, config=config)
            
            if self.driver and full_html:
                article.set_html(full_html)
            else:
                article.download()
                
            article.parse()
        except:
            return []
  
        full_text = article.text.replace('\n', ' ')
        full_words = full_text.split(' ')
        # Create a list of "documents". Each "document" is a string that contains document_size consecutive words from the article.
        documents = [' '.join(full_words[i:i+document_size]) for i in range(0, len(full_words), document_size)]
        # Remove documents that are too short
        documents = [doc for doc in documents if len(doc) > 100]
        return documents
    
    # Perform a Google search and retrieve the content of the top results. Maximum word count is num_documents * context_size (default 7680)
    def __call__(self, query, num_documents=NUM_DOCUMENTS):
        google_resp = self.search_google(query)
        
        documents = []
        top_links = []
        direct_answer = None

        # Check if 'answerBox' key exists in the response
        if 'answerBox' in google_resp and google_resp['answerBox']:
            url_found = False
            for key, value in google_resp['answerBox'].items():
                if isinstance(value, str) and "https://" in value:
                    documents += self.search_url(value)
                    top_links.append({
                        'title': google_resp['answerBox'].get('title', 'No title available'),
                        'link': value
                    })
                    url_found = True
                    break  
            if not url_found or len(documents) < 200:
                direct_answer = f"\n{json.dumps(google_resp['answerBox'], indent=2)}\n"
        # Check if knowledgeGraph key exists in the response
        elif 'knowledgeGraph' in google_resp and google_resp['knowledgeGraph']:
            direct_answer = f"\n{json.dumps(google_resp['knowledgeGraph'], indent=2)}\n"
        else:
            # Handling the case where there is no direct answer
            for i, resp in enumerate(google_resp.get('organic', [])):
                # Additional call to search_url to parse the content of the link
                documents += self.search_url(resp['link'])
                if i < 5:  # Only store top 5 links
                    top_links.append({
                        'title': resp['title'],
                        'link': resp['link']
                    })
                if len(documents) > num_documents:
                    break

        # Ensuring we only return the requested number of documents
        documents = documents[:num_documents]

        return documents, top_links, direct_answer

# Define a class to retrieve the most relevant documents for a question
class DocumentRetriever:
    # Create a vector embedding of a text using OpenAI's 'text-embedding-3-large' model
    def encode(self, input):
        resp = openai_client.embeddings.create(
            model = 'text-embedding-3-large',
            input = input
        )

        embeds = np.array([d.embedding for d in resp.data])
        return embeds
    
    # Retrieve the most relevant documents for a question using vector embeddings
    def __call__(self, question, documents, k=TOP_K_RESULTS):
        question_embed = self.encode(question)
        document_embeds = self.encode(documents)

        # Compute the cosine similarity between the question embedding and the document embeddings
        scores = np.dot(question_embed, document_embeds.T)
        # Sort the documents by their cosine similarity to the question
        ranks = np.argsort(-scores)[0, :k].tolist()
        # Retrieve the most relevant documents
        contexts = [documents[r] for r in ranks]
        return contexts

# Define a class to generate an answer to a question based on a set of documents
class Reader:
    def __call__(self,log_and_call_manager,chain_id,query, contexts):
        agent = 'Google Search Summarizer'
        text = ""
        
        try:
            # Attempt package-relative import
            from . import models, prompts
        except ImportError:
            # Fall back to script-style import
            import models, prompts
        
        # Construct prompt and messages
        for ctx in contexts:
            text += f'* {ctx}\n'

        # Check if PROMPT_TEMPLATES.json exists and load the prompts from there. If not, use the default prompts.
        if os.path.exists("PROMPT_TEMPLATES.json"):
            # Load from JSON file
            with open("PROMPT_TEMPLATES.json", "r") as f:
                prompt_data = json.load(f)
            prompt = prompt_data.get("google_search_summarizer_system", "")
            prompt = prompt.format(text, query)
        else:
            prompt = prompts.google_search_summarizer_system.format(text, query)
            
        search_messages = [{"role": "user", "content": prompt}]

        llm_response = models.llm_stream(log_and_call_manager,search_messages, agent=agent, chain_id=chain_id)

        return llm_response
    
class Search:
    def __init__(self):
        self.search_engine = SearchEngine()
        self.document_retriever = DocumentRetriever()
        self.reader = Reader()

    def _extract_search_query(self,question: str) -> str:
        search_query = re.sub('\'|"', '',  question).strip()
        return search_query

    def __call__(self, log_and_call_manager,chain_id,question):
        question=self._extract_search_query(question)
        documents,top_links,direct_answer = self.search_engine(question)
        if direct_answer:
            return direct_answer, None
        contexts = self.document_retriever(question, documents)
        answer = self.reader(log_and_call_manager,chain_id,question, contexts)

        return answer, top_links
    
### END SEARCH ACTIONS ###
    
class Calculator:
    def __call__(self, log_and_call_manager, chain_id, code):
        links = None
        # It's still important to be careful with eval(), as it can execute arbitrary code.
        try:
            return str(eval(code)),links
        except Exception as e:
            return str(e),links  # Return the error message as a string

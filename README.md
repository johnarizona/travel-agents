python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install strands-agents beautifulsoup4 playwright
playwright install

python hotel_agent.py marriott.txt
python airline_agent.py alaska.txt

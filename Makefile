.PHONY: mcp bot dashboard install install-mcp install-bot install-dashboard

mcp:
	cd mcp-server && python server.py

bot:
	cd bot && python main.py

dashboard:
	cd dashboard && streamlit run app.py

install-mcp:
	cd mcp-server && pip install -r requirements.txt

install-bot:
	cd bot && pip install -r requirements.txt

install-dashboard:
	cd dashboard && pip install -r requirements.txt

install: install-mcp install-bot install-dashboard

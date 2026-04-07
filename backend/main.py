import uvicorn
import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    # Get port from environment or default to 8080
    port = int(os.getenv("PORT", 8080))
    
    # Run the server
    # "app.api:app" means:
    #   app/      (folder)
    #   api.py    (file)
    #   app       (variable inside api.py)
    uvicorn.run("app.api:app", host="0.0.0.0", port=port, reload=False)
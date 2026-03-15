import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.country_code import CountryCode
from plaid.configuration import Configuration
from plaid.api_client import ApiClient
from . import models, schemas, database
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Plaid Client Setup
host = os.getenv('PLAID_ENV', 'sandbox') == 'sandbox' and Configuration.host_sandbox or \
       os.getenv('PLAID_ENV') == 'development' and Configuration.host_development or \
       Configuration.host_production

configuration = Configuration(
    host=host,
    api_key={
        'clientId': os.getenv('PLAID_CLIENT_ID'),
        'secret': os.getenv('PLAID_SECRET'),
    }
)

api_client = ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/create_link_token")
def create_link_token():
    try:
        request = LinkTokenCreateRequest(
            products=[Products('transactions'), Products('investments')],
            client_name="Personal Finance App",
            country_codes=[CountryCode('US')],
            language='en',
            user=LinkTokenCreateRequestUser(client_user_id='user-id-123')
        )
        response = client.link_token_create(request)
        return response.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/exchange_public_token")
def exchange_public_token(public_token: str):
    # This is where we would exchange the public token for an access token
    # and store it in the database.
    return {"message": "Token exchange logic placeholder"}

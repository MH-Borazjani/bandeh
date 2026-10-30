from fastapi import FastAPI
from pydantic import BaseModel, EmailStr

from auth_routes import router as auth_router

class Schema_model(BaseModel):
    email:EmailStr
    password:str

app=FastAPI()

app.include_router(auth_router)

@app.post("/signup")
async def sin(cred:Schema_model):
    
    return cred.model_dump()
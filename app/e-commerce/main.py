from fastapi import FastAPI
from pydantic import BaseModel, EmailStr

class Schema_model(BaseModel):
    email:EmailStr
    password:str

app=FastAPI()

@app.post("/signup")
async def sin(cred:Schema_model):
    
    return cred.model_dump()
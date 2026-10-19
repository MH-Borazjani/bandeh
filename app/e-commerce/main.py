from fastapi import FastAPI
import httpx

app = FastAPI()

# نام سرویس در docker-compose دایرکتوس است
DIRECTUS_URL = "http://directus:8055"


@app.get("/hello_mother")
async def get_products():
    async with httpx.AsyncClient() as client:
        # درخواست به اندپوینت رسمی دایرکتوس برای خواندن لیست کالاها
        response = await client.get(f"{DIRECTUS_URL}/items/hello_mother")

        # خروجی JSON خام را پس می‌گیریم
        # data = response.json()
        # return [data["data"], "mohammad"]
        return  response.json()

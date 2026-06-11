import requests
from bs4 import BeautifulSoup
import os

API_KEY = os.environ.get("GOOGLE_API_KEY")
CX_ID = os.environ.get("GOOGLE_CX_ID")
QUERY = "Python"

params = {
    "key": API_KEY,
    "cx": CX_ID,
    "q": QUERY
}

key_words = [
    "trump",
    "Israel"
]

url = "https://cse.google.com/cse?cx=e26d7e4e0bc774ea6"

response = requests.get(url, params=params)
soup = BeautifulSoup(response.content, "lxml")
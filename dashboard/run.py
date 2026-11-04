"""Local development server: python run.py → http://127.0.0.1:5070/"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5070, debug=True)

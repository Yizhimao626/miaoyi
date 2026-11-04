import os


def create_database_path():
    db_path = os.environ.get('DATABASE_PATH', 'miaoyi.db')
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    print(f"Database path ready: {db_path}")
    return db_path

def create_model_path():
    model_path = os.environ.get('MODEL_PATH', 'models')
    if not os.path.exists(model_path):
        os.makedirs(model_path, exist_ok=True)
    print(f"Model path ready: {model_path}")
    return model_path

def copy_models_if_needed():
    model_path = os.environ.get('MODEL_PATH', 'models')
    if not os.path.exists(os.path.join(model_path, 'paraphrase-MiniLM-L6-v2')):
        print("Model files not found. Downloading...")
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('paraphrase-MiniLM-L6-v2')
            model.save(os.path.join(model_path, 'paraphrase-MiniLM-L6-v2'))
            print("Model downloaded and saved successfully")
        except Exception as e:
            print(f"Warning: Could not download model: {e}")
            print("The app will use fallback emotion memory mode")

if __name__ == '__main__':
    print("=== 喵艺服务器初始化 ===")
    create_database_path()
    create_model_path()
    copy_models_if_needed()
    print("=== 初始化完成 ===")

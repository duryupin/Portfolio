import requests
import time
from pathlib import Path

BASE = "https://api.faufcc.ru/api"
REGISTRY_ID = "c5020d9a-a239-4806-a793-ac66cb36c71a"
DOWNLOAD_DIR = Path("pdf_files")
DOWNLOAD_DIR.mkdir(exist_ok=True)
SESSION = requests.Session()


def safe_name(name: str) -> str:
    """Сделать имя файла безопасным"""
    return "".join(c if c.isalnum() or c in " _-." else "_" for c in name).strip()


def get_categories(parent=None):
    """Получить список категорий для нашего REGISTRY_ID"""
    params = {"registry": REGISTRY_ID}
    if parent:
        params["parent"] = parent
    r = SESSION.get(f"{BASE}/registries/categories", params=params)
    r.raise_for_status()
    return r.json().get("data", [])


def get_entries(category_id):
    """Получить все документы в категории"""
    entries = []
    page = 1
    while True:
        params = {
            "page": page,
            "filters": str({"registry": REGISTRY_ID, "category": category_id}).replace("'", '"'),
            "include": "asset"
        }
        r = SESSION.get(f"{BASE}/registries/entries", params=params)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", [])
        if not items:
            break
        entries.extend(items)
        pagination = data.get("meta", {}).get("pagination", {})
        if page >= pagination.get("totalPages", 1):
            break
        page += 1
    return entries


def download_file(url, path):
    """Скачать PDF по URL"""
    if path.exists():
        print(f"⚡ Уже скачан: {path.name}")
        return
    try:
        with SESSION.get(url, stream=True) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
        print(f"✅ Скачан: {path.name}")
        time.sleep(0.2)  # небольшая задержка
    except Exception as e:
        print(f"❌ Ошибка при скачивании {url}: {e}")


def process_category(cat, depth=0):
    cid = cat["id"]
    name = cat.get("name", "Без имени")

    # Получаем все документы в категории
    entries = get_entries(cid)
    print("  " * depth + f"- {name} ({cid}) → {len(entries)} документов")
    for entry in entries:
        asset_data = entry.get("asset", {}).get("data")
        if not asset_data:
            continue
        download_url = asset_data.get("links", {}).get("download")
        if not download_url:
            continue
        file_name = asset_data.get("filename") or f"{entry.get('number')}.pdf"
        save_name = f"{safe_name(file_name)}_{entry.get('id')}.pdf"
        save_path = DOWNLOAD_DIR / save_name
        download_file(download_url, save_path)

    # Рекурсивно обходим подкатегории
    children = get_categories(parent=cid)
    for child in children:
        process_category(child, depth + 1)


def main():
    cats = get_categories()
    for cat in cats:
        process_category(cat)
    print("🎉 Скачивание завершено!")


if __name__ == "__main__":
    main()

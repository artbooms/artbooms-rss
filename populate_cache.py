# esegui una sola volta per popolare l'intero archivio
from article_processor import generate_items
if __name__ == "__main__":
    print("POPOLAMENTO CACHE: attenzione, può richiedere tempo per molte pagine.")
    generate_items(force=True)
    print("Fatto.")

# Instalar dependências
import os
import json
import requests
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import random

# Variáveis de ambiente
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# Função para obter token de acesso do IGDB
def get_igdb_access_token(client_id, client_secret):
    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials"
    }
    response = requests.post(url, data=payload)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        raise Exception("Erro ao obter o token de acesso do IGDB")

ACCESS_TOKEN = get_igdb_access_token(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
print(f"Token de acesso gerado: {ACCESS_TOKEN}")

# Função para verificar espaço disponível em um drive usando CLI
def check_drive_space(drive_name):
    result = os.popen(f'rclone size "{drive_name}:" --json').read()
    if result:
        try:
            status = json.loads(result)
            used_space = int(status.get("bytes", 0))  # Bytes usados no drive
            total_space = int(status.get("total_bytes", 999999999999))  # Assume espaço ilimitado se ausente
            free_space = total_space - used_space
            return free_space
        except Exception as e:
            print(f"Erro ao processar a resposta do drive {drive_name}: {e}")
            return 999999999999  # Assume espaço ilimitado em caso de erro
    else:
        print(f"Erro ao verificar o espaço disponível no drive: {drive_name}")
        return 999999999999  # Assume espaço ilimitado em caso de erro

# Função para escolher o próximo drive com espaço disponível
def choose_next_drive(drive_names):
    for drive in drive_names:
        try:
            free_space = check_drive_space(drive)
            if free_space > 1e9:  # Verifica se há pelo menos 1GB livre
                return drive
        except Exception as e:
            print(f"Erro ao verificar espaço no drive {drive}: {e}")
    raise Exception("Todos os drives estão cheios!")

# Função para baixar imagens em lotes de 500 e enviar para o drive
def download_and_save_screenshots_in_batches(screenshots, drive_names, checkpoint_file="screenshot_checkpoint.json"):
    # Carregar checkpoint, se existir
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, "r") as f:
            checkpoint = json.load(f)
            downloaded_screenshots = set(checkpoint.get("downloaded", []))
    else:
        downloaded_screenshots = set()

    # Criar o diretório local para screenshots temporários
    os.makedirs("./screenshots", exist_ok=True)

    def download_image(screenshot):
        try:
            screenshot_id = screenshot["id"]
            if screenshot_id in downloaded_screenshots:
                return None  # Ignorar screenshots já baixados

            # Baixar a imagem
            image_url = screenshot["url"].replace("t_thumb", "t_screenshot_big")
            if not image_url.startswith("http"):
                image_url = "https:" + image_url
            response = requests.get(image_url)
            if response.status_code != 200:
                print(f"Erro ao baixar screenshot {screenshot_id}")
                return None

            image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                print(f"Erro ao decodificar screenshot {screenshot_id}")
                return None

            image = cv2.resize(image, (224, 224))

            # Salvar a imagem localmente
            screenshot_path = f"./screenshots/{screenshot_id}.jpg"
            cv2.imwrite(screenshot_path, image)
            return screenshot_id
        except Exception as e:
            print(f"Erro ao processar screenshot {screenshot['id']}: {e}")
            return None

    # Processar screenshots em lotes de 500
    batch_size = 500
    for i in range(0, len(screenshots), batch_size):
        batch = screenshots[i:i + batch_size]
        print(f"Processando lote {i // batch_size + 1} de {len(screenshots) // batch_size + 1}...")

        # Baixar imagens do lote
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(download_image, screenshot) for screenshot in batch]
            for future in as_completed(futures):
                screenshot_id = future.result()
                if screenshot_id:
                    downloaded_screenshots.add(screenshot_id)

        # Escolher o próximo drive com espaço disponível
        drive_name = choose_next_drive(drive_names)

        # Enviar o lote para o drive
        print(f"Enviando lote para o drive {drive_name}...")
        os.system(f'rclone copy "./screenshots/" "{drive_name}:screenshots/"')

        # Excluir os arquivos locais após o upload
        for screenshot in batch:
            screenshot_id = screenshot["id"]
            screenshot_path = f"./screenshots/{screenshot_id}.jpg"
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)

        # Salvar checkpoint
        with open(checkpoint_file, "w") as f:
            json.dump({"downloaded": list(downloaded_screenshots)}, f, indent=4)

        print(f"Lote {i // batch_size + 1} enviado com sucesso para o drive {drive_name}.")

    print("Todos os screenshots foram baixados e salvos nos drives.")

# Função para verificar screenshots já salvos nos drives
def recover_existing_screenshots(drive_names):
    existing_screenshots = set()
    for drive in drive_names:
        print(f"Verificando screenshots no drive {drive}...")
        result = os.popen(f'rclone lsjson "{drive}:screenshots/"').read()
        files = json.loads(result)
        for file in files:
            screenshot_id = os.path.splitext(file["Path"])[0]
            existing_screenshots.add(screenshot_id)
    return existing_screenshots

# Função para criar um novo checkpoint a partir dos dados do drive
def create_checkpoint_from_drive(drive_names, checkpoint_file="screenshot_checkpoint.json"):
    # Recuperar screenshots existentes nos drives
    existing_screenshots = recover_existing_screenshots(drive_names)

    # Criar um novo checkpoint
    checkpoint = {"downloaded": list(existing_screenshots)}
    with open(checkpoint_file, "w") as f:
        json.dump(checkpoint, f, indent=4)
    print(f"Checkpoint criado com {len(existing_screenshots)} screenshots recuperados.")

# Função principal para baixar screenshots
def fetch_game_screenshots(output_file="game_screenshots.json", verbose=True, checkpoint_file="checkpoint.json"):
    url = "https://api.igdb.com/v4/games"
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }

    # Verificar se o checkpoint existe; caso contrário, criar a partir dos drives
    if not os.path.exists(checkpoint_file):
        print("Checkpoint não encontrado. Tentando recuperar screenshots dos drives...")
        drive_names = [f"drive{i}" for i in range(1, 13)]
        create_checkpoint_from_drive(drive_names, checkpoint_file)

    # Carregar checkpoint
    with open(checkpoint_file, "r") as f:
        checkpoint = json.load(f)
        downloaded_screenshots = set(checkpoint.get("downloaded", []))
        offset = checkpoint.get("offset", 0)
        if verbose:
            print(f"Checkpoint carregado. Continuando a partir do offset {offset}...")

    game_data = []

    batch_size = 500  # Limite máximo por consulta
    drive_names = [f"drive{i}" for i in range(1, 13)]  # Nomes dos drives

    while True:
        # Adicionar filtros para evitar processar sempre os mesmos dados
        data = f'fields id, name, screenshots.*; where first_release_date > 0 & category = 0; limit {batch_size}; offset {offset};'
        response = requests.post(url, headers=headers, data=data)
        
        if response.status_code == 200:
            games = response.json()
            if not games:
                if verbose:
                    print("Todos os jogos foram processados.")
                break
            
            for game in games:
                if "screenshots" in game and game["screenshots"]:
                    limited_screenshots = game["screenshots"][:5]  # Limita a 5 screenshots
                    game_info = {
                        "id": game["id"],
                        "name": game["name"],
                        "screenshots": [
                            {"id": screenshot["id"], "url": screenshot["url"]}
                            for screenshot in limited_screenshots
                        ]
                    }
                    game_data.append(game_info)
            
            if verbose:
                print(f"Processados {len(game_data)} jogos até agora...")
            
            # Baixar screenshots em lote e salvar nos drives
            all_screenshots = [screenshot for game in games for screenshot in game.get("screenshots", [])[:5]]
            new_screenshots = [s for s in all_screenshots if s["id"] not in downloaded_screenshots]
            download_and_save_screenshots_in_batches(new_screenshots, drive_names)
            
            # Salvar checkpoint
            checkpoint = {"games": game_data, "offset": offset + batch_size, "downloaded": list(downloaded_screenshots)}
            with open(checkpoint_file, "w") as f:
                json.dump(checkpoint, f, indent=4)
            
            offset += batch_size
        else:
            raise Exception("Erro ao buscar informações de jogos no IGDB")
    
    # Remover checkpoint após conclusão
    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)
    
    with open(output_file, "w") as f:
        json.dump(game_data, f, indent=4)
    if verbose:
        print(f"Informações de {len(game_data)} jogos e screenshots salvas em {output_file}")

# Executar o sistema
fetch_game_screenshots(verbose=True)

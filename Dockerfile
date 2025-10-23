FROM python:3.11-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les requirements et installer les dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code de l'application (maintenant dans app/)
COPY app/ ./app/

# Créer le dossier static (sera monté en volume)
RUN mkdir -p /app/static

# Exposer le port
EXPOSE 8000

# Commande de démarrage (notez app.main:app au lieu de main:app)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
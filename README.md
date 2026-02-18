# PyRA - RetroAchievements Tracker

PyRA est une application desktop (Python/Tkinter) pour suivre un compte RetroAchievements, sans passer par une page web.

## Fonctionnalites

- Fenetre de connexion (cle API + nom d'utilisateur API)
- Synchronisation automatique apres connexion
- Detection d'emulateur compatible RA (`Live` ou `Inactif ou inconnu`)
- Onglet `Jeu en cours` avec:
  - resume du jeu (titre, console, progression, dernier succes)
  - premier succes non debloque
  - galerie des succes du jeu en cours avec infobulles
- Mode `Light | Dark`
- Interface responsive
- Sauvegarde de la position de la fenetre

## Elements en maintenance

- `Progression par jeu`
- `Succes recents`
- `Profil` (bouton + entree dans `Fichier`)

## TODO

- Corriger et etendre la traduction des descriptions de succes (qualite inegale et non appliquee partout).
- Ajouter un bouton d'ordre des succes avec 3 modes: `Normal`, `Easy -> Hard` (base sur la faisabilite, uniquement sur les succes non debloques), `Hard -> Easy` (base sur la faisabilite, uniquement sur les succes non debloques). Regle a conserver: `succes non debloques` en premier.

## Prerequis

- Windows
- Python 3.10+
- Connexion Internet
- Cle API RetroAchievements

## Installation (source)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Lancer l'application

```powershell
python main.py
```

Au premier demarrage:

1. Ouvrir `Connexion`
2. Saisir la cle API
3. Saisir le nom d'utilisateur API
4. Cliquer sur `Enregistrer`

La synchronisation demarre ensuite automatiquement.

## Rich Presence (important)

Pour que PyRA detecte correctement l'activite en cours (`Live`), il faut activer le **Rich Presence** dans l'emulateur.

Emplacements les plus courants:

- DuckStation: `Tools` > `Achievements` (activer Rich Presence)
- PCSX2: `Tools` > `Achievements` (activer Rich Presence)
- Dolphin: `Tools` > `Achievements` (activer Rich Presence)
- BizHawk: `Tools` > `RetroAchievements` (activer Rich Presence)
- PPSSPP: `Settings` > `Tools` > `Achievements` (activer Rich Presence)

Pour les autres emulateurs compatibles RetroAchievements:

- Ouvrir les parametres `Achievements` ou `RetroAchievements`
- Activer `Rich Presence`
- Verifier que vous etes connecte au meme compte RetroAchievements que dans PyRA

## Generer l'executable `.exe`

```powershell
.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Sortie:

- `dist\PyRA.exe`

## Donnees locales

- Configuration: `%APPDATA%\PyRA\config.json`
- Base SQLite: `%APPDATA%\PyRA\tracker.db`

## Variables d'environnement supportees

- `RA_API_KEY`
- `RA_API_USERNAME`
- `TRACKED_USERNAME`
- `TRACKER_DB_PATH`

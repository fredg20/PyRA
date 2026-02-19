# PyRA - Suivi RetroAchievements

PyRA est une application desktop (Python/Tkinter) pour suivre un compte RetroAchievements sans passer par un navigateur.

Version actuelle: `0.9.0-beta.3`

## FonctionnalitÃ©s actuelles

- Connexion API RetroAchievements (clÃ© API + nom d'utilisateur API).
- Synchronisation automatique du tableau de bord.
- DÃ©tection d'Ã©tat en temps rÃ©el:
  - `Inactif` si aucun Ã©mulateur compatible n'est chargÃ©.
  - `Ã‰mulateur chargÃ©` dÃ¨s qu'un Ã©mulateur compatible est dÃ©tectÃ©.
  - `Jeu chargÃ©` dÃ¨s qu'un jeu actif est dÃ©tectÃ© cÃ´tÃ© Ã©mulateur.
  - Retour Ã  `Ã‰mulateur chargÃ©` quand le jeu se ferme (Ã©mulateur encore ouvert).
  - Retour Ã  `Inactif` quand l'Ã©mulateur se ferme.
- Onglet `Jeu en cours`:
  - rÃ©sumÃ© du jeu (titre, console, progression, dernier succÃ¨s),
  - section `SuccÃ¨s Ã  dÃ©bloquer` avec badge, description et navigation,
  - ordre des succÃ¨s verrouillÃ©s: `Normal`, `Facile -> Difficile`, `Difficile -> Facile`,
  - galerie des succÃ¨s du jeu avec dÃ©filement automatique.
- Interface `Light | Dark`, style visuel unifiÃ©, coins arrondis harmonisÃ©s.
- Sauvegarde/restauration de la gÃ©omÃ©trie de fenÃªtre.

## Sections en maintenance

- `Progression par jeu`
- `SuccÃ¨s rÃ©cents`
- FenÃªtre `Profil` (accessible, mais encore en maintenance)

## PrÃ©requis

- Windows
- Python 3.10+
- Connexion Internet
- ClÃ© API RetroAchievements

## Périmètre émulateurs supportés

PyRA cible un usage desktop Windows. La détection d'état émulateur/jeu est
limitée aux émulateurs pertinents pour ce contexte.

Hors périmètre pour ce projet:

- Émulateurs Android uniquement
- Émulateurs mobiles uniquement (Android/iOS)
- Émulateurs UWP/Xbox
- Émulateurs non retenus pour le scope actuel

Ce choix est volontaire pour garder une détection stable, rapide et
maintenable dans PyRA.

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

Au premier dÃ©marrage:

1. Ouvrir `Connexion`
2. Saisir la clÃ© API
3. Saisir le nom d'utilisateur API
4. Cliquer sur `Enregistrer`

## Rich Presence (important)

Pour que PyRA dÃ©tecte correctement l'activitÃ© en cours, activez **Rich Presence** dans l'Ã©mulateur.

Exemples frÃ©quents:

- DuckStation: `Tools` > `Achievements`
- PCSX2: `Tools` > `Achievements`
- Dolphin: `Tools` > `Achievements`
- BizHawk: `Tools` > `RetroAchievements`
- PPSSPP: `Settings` > `Tools` > `Achievements`

## GÃ©nÃ©rer l'exÃ©cutable `.exe`

```powershell
.venv\Scripts\Activate.ps1
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Sortie:

- `dist\PyRA.exe`

## DonnÃ©es locales

- Configuration: `%APPDATA%\PyRA\config.json`
- Base SQLite: `%APPDATA%\PyRA\tracker.db`
- Cache jeu en cours: `%APPDATA%\PyRA\current_game_cache.json`
- Journal debug: `%APPDATA%\PyRA\debug.log`

## Variables d'environnement supportÃ©es

- `RA_API_KEY`
- `RA_API_USERNAME`
- `TRACKED_USERNAME`
- `TRACKER_DB_PATH`



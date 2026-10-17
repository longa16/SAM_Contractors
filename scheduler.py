"""
Planificateur automatisé pour SAM.gov
Fait tourner le script de synchronisation 3 fois par jour.
Idéal pour être lancé en arrière-plan.
"""

import time
import traceback
from datetime import datetime, timedelta
from sync import run_sync

# Configuration : 3 fois par jour = toutes les 8 heures
INTERVAL_HOURS = 8
INTERVAL_SECONDS = INTERVAL_HOURS * 3600

def main():
    print("=" * 60)
    print("Démarrage du Superviseur/Scheduler SAM.gov")
    print(f" Fréquence : 1 exécution toutes les {INTERVAL_HOURS} heures (3x/jour)")
    print("=" * 60)
    
    while True:
        try:
            print(f"\n[SCHEDULER] ▶ Lancement de la tâche à {datetime.now().strftime('%H:%M:%S')}...")
            
            # Appel direct de la fonction de synchronisation
            run_sync()
            
            # Calcul du prochain lancement
            next_run = datetime.now() + timedelta(seconds=INTERVAL_SECONDS)
            print(f"\n[SCHEDULER] Prochaine extraction prévue à {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"[SCHEDULER] Mise en veille du script pour {INTERVAL_HOURS}h...")
            
            # Mise en pause
            time.sleep(INTERVAL_SECONDS)
            
        except KeyboardInterrupt:
            print("\n[SCHEDULER] Arrêt manuel demandé par l'utilisateur.")
            break
        except Exception as e:
            print(f"\n[ERREUR CRITIQUE] Le job de synchronisation a crashé : {e}")
            traceback.print_exc()
            print("[SCHEDULER] Nouvel essai de secours dans 15 minutes...")
            time.sleep(15 * 60)  # Retry dans 15 minutes en cas d'erreur API temporaire

if __name__ == "__main__":
    main()

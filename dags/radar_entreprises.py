"""Graphe quotidien du radar economique de l'Herault.

Le DAG n'ecrit aucune regle metier : il declare l'ordre, les reprises et les
dependances. Chaque tache appelle une fonction de `radar.pipeline`, testable
sans ordonnanceur.

Fenetre d'execution : le BODACC parait du mardi au samedi matin. Le graphe se
declenche a 06:00 Europe/Paris et traite la parution de la veille
(`data_interval_end` moins un jour), pour laisser a la DILA le temps de publier.
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task
from airflow.models import Param

from radar import pipeline

DOC = __doc__

ARGUMENTS = {
    "owner": "yasmina",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": pendulum.duration(minutes=30),
    "depends_on_past": False,
}


def _jour(contexte) -> str:
    """Date de parution traitee : parametre explicite, sinon date logique.

    Passer par la date logique et non par `date.today()` est ce qui rend le
    graphe rejouable : relancer le 12 mars retraite exactement la parution du
    12 mars, avec les memes donnees et les memes chiffres.
    """
    parametres = contexte.get("params") or {}
    if parametres.get("jour"):
        return str(parametres["jour"])
    return contexte["logical_date"].in_timezone("Europe/Paris").format("YYYY-MM-DD")


@dag(
    dag_id="radar_entreprises_herault",
    description=(
        "Creations, cessations, defaillances et transferts d'entreprises de l'Herault, "
        "par commune et par secteur"
    ),
    doc_md=DOC,
    schedule="0 6 * * 2-6",  # du mardi au samedi, apres la parution du BODACC
    start_date=pendulum.datetime(2026, 9, 1, tz="Europe/Paris"),
    catchup=False,
    max_active_runs=1,
    default_args=ARGUMENTS,
    tags=["bodacc", "sirene", "herault", "s3", "qualite"],
    params={"jour": Param("", type="string", description="Date de parution AAAA-MM-JJ, vide = date logique")},
)
def radar_entreprises_herault():
    @task(task_id="preparer")
    def preparer(**contexte):
        return pipeline.etape_preparer(_jour(contexte))

    @task(task_id="extraire_bodacc")
    def extraire(**contexte):
        return pipeline.etape_extraire(_jour(contexte))

    @task(task_id="enrichir_sirene")
    def enrichir(**contexte):
        return pipeline.etape_enrichir(_jour(contexte))

    @task(task_id="construire_silver")
    def silver(**contexte):
        return pipeline.etape_silver(_jour(contexte))

    # Pas de reprise sur les controles : un echec de qualite est deterministe.
    # Le rejouer ne change rien et fait seulement attendre. Mesure en integration
    # continue : une parution refusee coutait 1 676 secondes avec deux reprises
    # et une attente exponentielle, contre quelques secondes sans.
    @task(task_id="controles_qualite_silver", retries=0)
    def controler_silver(**contexte):
        # Tache bloquante : une exception ici arrete le graphe avant la couche gold.
        return pipeline.etape_controler_silver(_jour(contexte))

    @task(task_id="construire_gold")
    def gold(**contexte):
        return pipeline.etape_gold(_jour(contexte))

    @task(task_id="controles_qualite_gold", retries=0)
    def controler_gold(**contexte):
        return pipeline.etape_controler_gold(_jour(contexte))

    @task(task_id="publier_indicateurs")
    def publier(**contexte):
        return pipeline.etape_publier(_jour(contexte))

    # L'enrichissement est facultatif pour la suite : la couche argent sait se
    # passer des fiches SIRENE, mais on l'attend pour ne pas publier un jour
    # sans NAF alors qu'il aurait pu en avoir.
    (
        preparer()
        >> extraire()
        >> enrichir()
        >> silver()
        >> controler_silver()
        >> gold()
        >> controler_gold()
        >> publier()
    )


radar_entreprises_herault()

-- Requetes de publication du radar.
--
-- Un seul texte SQL, execute soit par Athena sur le catalogue Glue, soit par
-- DuckDB sur les memes fichiers Parquet. Le SQL reste volontairement standard :
-- pas de fonction propre a un moteur, pas de CTE recursive, pas de LATERAL.
-- Le decoupage se fait sur les marqueurs « -- nom: <cle> », lus par
-- radar.warehouse.charger_requetes.
--
-- Deux tables (Athena) ou deux vues (DuckDB) :
--   evenements  : couche argent, un evenement BODACC par ligne
--   indicateurs : couche or, une ligne par (jour, commune, section NAF)
-- La colonne date_parution vient de la partition, jamais du fichier.

-- nom: solde_net_departement
SELECT
    date_parution,
    SUM(nb_creations)        AS creations,
    SUM(nb_immatriculations) AS immatriculations,
    SUM(nb_transferts)       AS transferts,
    SUM(nb_ventes_fonds)     AS ventes_de_fonds,
    SUM(nb_radiations)       AS radiations,
    SUM(nb_defaillances)     AS defaillances,
    SUM(nb_liquidations)     AS liquidations,
    SUM(nb_evenements)       AS evenements,
    SUM(solde_net)           AS solde_net
FROM indicateurs
WHERE date_parution = '{jour}'
GROUP BY date_parution;

-- nom: top_communes
SELECT
    nom_commune,
    code_commune,
    population_commune,
    SUM(nb_creations)    AS creations,
    SUM(nb_radiations)   AS radiations,
    SUM(nb_defaillances) AS defaillances,
    SUM(nb_evenements)   AS evenements,
    SUM(solde_net)       AS solde_net
FROM indicateurs
WHERE date_parution = '{jour}'
  AND code_commune IS NOT NULL
GROUP BY nom_commune, code_commune, population_commune
ORDER BY evenements DESC, nom_commune ASC;

-- nom: par_section
SELECT
    section_naf,
    libelle_section_naf,
    SUM(nb_creations)    AS creations,
    SUM(nb_radiations)   AS radiations,
    SUM(nb_defaillances) AS defaillances,
    SUM(nb_evenements)   AS evenements,
    SUM(solde_net)       AS solde_net
FROM indicateurs
WHERE date_parution = '{jour}'
GROUP BY section_naf, libelle_section_naf
ORDER BY evenements DESC, section_naf ASC;

-- nom: signaux_du_jour
SELECT
    denomination,
    siren,
    type_evenement,
    gravite,
    nom_commune,
    code_commune,
    libelle_section_naf,
    tribunal,
    date_jugement,
    url_annonce
FROM evenements
WHERE date_parution = '{jour}'
  AND gravite >= 2
ORDER BY gravite DESC, nom_commune ASC, denomination ASC;

-- nom: prospects_creations
SELECT
    denomination,
    siren,
    nom_commune,
    libelle_section_naf,
    activite_declaree,
    url_annonce
FROM evenements
WHERE date_parution = '{jour}'
  AND type_evenement IN ('creation', 'immatriculation')
ORDER BY nom_commune ASC, denomination ASC;

-- nom: ventes_de_fonds
SELECT
    denomination,
    siren,
    nom_commune,
    libelle_section_naf,
    montant_vente_eur,
    url_annonce
FROM evenements
WHERE date_parution = '{jour}'
  AND type_evenement = 'vente_fonds'
ORDER BY montant_vente_eur DESC NULLS LAST, denomination ASC;

-- nom: controle_croise_silver_gold
SELECT
    (SELECT COUNT(*) FROM evenements WHERE date_parution = '{jour}')             AS lignes_argent,
    (SELECT SUM(nb_evenements) FROM indicateurs WHERE date_parution = '{jour}')  AS evenements_or,
    (SELECT COUNT(*) FROM indicateurs WHERE date_parution = '{jour}')            AS lignes_or;

-- Declaration des tables externes du catalogue Glue, pour Athena.
--
-- A rejouer une seule fois apres `terraform apply`, via
-- `python -m radar.cli catalogue` (moteur athena). La projection de partition
-- (`projection.enabled`) evite d'avoir a lancer MSCK REPAIR TABLE ou un
-- ALTER TABLE ADD PARTITION apres chaque execution du graphe : Athena deduit
-- les partitions de la plage de dates declaree ici.
--
-- Les placeholders {bucket}, {prefixe} et {base} sont remplis par
-- radar.warehouse.declarer_tables_athena.

CREATE EXTERNAL TABLE IF NOT EXISTS {base}.evenements (
    id_annonce               string,
    type_evenement           string,
    famille_bodacc           string,
    gravite                  int,
    siren                    string,
    denomination             string,
    activite_declaree        string,
    code_commune             string,
    nom_commune              string,
    code_postal              string,
    population_commune       bigint,
    latitude                 double,
    longitude                double,
    tribunal                 string,
    date_jugement            string,
    montant_vente_eur        double,
    section_naf              string,
    libelle_section_naf      string,
    code_naf                 string,
    tranche_effectif         string,
    date_creation_entreprise string,
    url_annonce              string
)
PARTITIONED BY (date_parution string)
STORED AS PARQUET
LOCATION 's3://{bucket}/{prefixe}/silver/evenements/'
TBLPROPERTIES (
    'parquet.compression' = 'SNAPPY',
    'projection.enabled' = 'true',
    'projection.date_parution.type' = 'date',
    'projection.date_parution.format' = 'yyyy-MM-dd',
    'projection.date_parution.range' = '2024-01-01,NOW',
    'projection.date_parution.interval' = '1',
    'projection.date_parution.interval.unit' = 'DAYS',
    'storage.location.template' = 's3://{bucket}/{prefixe}/silver/evenements/date_parution=${date_parution}/'
);

CREATE EXTERNAL TABLE IF NOT EXISTS {base}.indicateurs (
    code_commune        string,
    nom_commune         string,
    population_commune  bigint,
    latitude            double,
    longitude           double,
    section_naf         string,
    libelle_section_naf string,
    nb_creations        int,
    nb_immatriculations int,
    nb_transferts       int,
    nb_ventes_fonds     int,
    nb_modifications    int,
    nb_radiations       int,
    nb_sauvegardes      int,
    nb_redressements    int,
    nb_liquidations     int,
    nb_plans_cession    int,
    nb_defaillances     int,
    nb_depots_comptes   int,
    nb_evenements       int,
    solde_net           int,
    montant_ventes_eur  double
)
PARTITIONED BY (date_parution string)
STORED AS PARQUET
LOCATION 's3://{bucket}/{prefixe}/gold/indicateurs_commune_secteur/'
TBLPROPERTIES (
    'parquet.compression' = 'SNAPPY',
    'projection.enabled' = 'true',
    'projection.date_parution.type' = 'date',
    'projection.date_parution.format' = 'yyyy-MM-dd',
    'projection.date_parution.range' = '2024-01-01,NOW',
    'projection.date_parution.interval' = '1',
    'projection.date_parution.interval.unit' = 'DAYS',
    'storage.location.template' = 's3://{bucket}/{prefixe}/gold/indicateurs_commune_secteur/date_parution=${date_parution}/'
);

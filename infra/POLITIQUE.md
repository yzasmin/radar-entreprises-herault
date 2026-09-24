Ce que fait `politique-minimale.json`, ce qu'il refuse, et comment l'attacher a la cle du graphe.

# Droits minimaux de la cle applicative

Le fichier `infra/politique-minimale.json` est pret a coller dans la console IAM
(**IAM > Politiques > Creer une politique > onglet JSON**). Il ne contient aucun
identifiant : c'est une description de droits, pas un secret.

Compartiment cible : **`amzn-s3-seau`**, region **`eu-north-1`** (Stockholm).
Prefixe du projet : **`radar/`**, et rien en dehors.

## Ce que la politique autorise, et rien d'autre

| Action | Sur quoi | Pourquoi le graphe en a besoin |
| --- | --- | --- |
| `s3:ListBucket` | `arn:aws:s3:::amzn-s3-seau`, **avec une condition `s3:prefix` limitee a `radar/`** | Lister les partitions deja ecrites, construire la fenetre glissante, compter les objets et leur taille |
| `s3:GetBucketLocation` | le meme compartiment | boto3 et DuckDB determinent la region du compartiment avant la premiere lecture |
| `s3:GetObject` | `arn:aws:s3:::amzn-s3-seau/radar/*` | Relire les couches bronze et silver entre deux taches, et lire les Parquet depuis DuckDB |
| `s3:PutObject` | idem | Ecrire les couches bronze, silver, gold, les rapports de qualite et la publication |
| `s3:DeleteObject` | idem | La commande de nettoyage `python -m radar.cli nettoyer`. Le graphe lui-meme n'en a pas besoin : il ecrase une partition en reecrivant la meme cle |
| `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts` | idem | Nettoyer un envoi interrompu ; boto3 passe en envoi fractionne au-dela de 8 Mo |

## Ce que la politique refuse explicitement

Deux instructions `Deny`, parce que dans IAM un refus explicite l'emporte toujours sur une
autorisation, meme si une politique plus large etait attachee un jour au meme utilisateur.

1. **Tout ce qui sort du compartiment et de son prefixe.** `Deny` sur `s3:*` avec `NotResource`
   limite a `amzn-s3-seau` et `amzn-s3-seau/radar/*`. Si le compartiment sert aussi a autre chose, le
   radar ne peut ni le lire ni le toucher.
2. **Toute action sur le compartiment lui-meme.** `s3:DeleteBucket`, `s3:DeleteBucketPolicy`,
   `s3:PutBucketPolicy`, `s3:PutBucketAcl`, `s3:PutBucketPublicAccessBlock`, `s3:PutBucketVersioning`.
   Une cle applicative n'a aucune raison de pouvoir supprimer un compartiment, ni de pouvoir lever le
   blocage d'acces public. C'est la protection la plus utile de toute la politique.

Ne figurent pas non plus dans les autorisations :

- `s3:CreateBucket` : le compartiment existe deja.
- `s3:ListAllMyBuckets` : la cle ne doit meme pas savoir quels autres compartiments existent.
  **Verifie sur la cle reelle** : `list_buckets` renvoie `AccessDenied`, et le diagnostic du projet
  l'affiche comme tel au lieu de s'arreter (`python -m radar.cli diagnostic`).
- Tout autre service : ni IAM, ni EC2, ni Lambda, ni CloudWatch.

## Athena et Glue : pas encore, et volontairement

La politique livree ici couvre **le chemin S3 plus DuckDB**, qui est la configuration publiee.
Le jour ou Athena est branche (`RADAR_MOTEUR=athena`), il faut ajouter les deux blocs ecrits dans
`infra/terraform/iam.tf` : `athena:StartQueryExecution` et les quatre actions de lecture associees sur
le seul workgroup du projet, puis les actions `glue:Get*`, `glue:CreateTable` et `glue:UpdateTable` sur
la seule base `radar_entreprises`. Ils y sont deja, en Terraform, prets a appliquer. Tant qu'Athena
n'est pas utilise, ces droits ne servent a rien et ne sont donc pas accordes.

## Marche a suivre, une fois la politique creee

1. **IAM > Utilisateurs > Creer un utilisateur**, **sans acces a la console** : cet utilisateur n'est
   qu'une cle de programme.
2. Attacher la politique creee a partir de `politique-minimale.json`.
3. **Onglet Informations d'identification de securite > Creer une cle d'acces**, cas d'usage
   « Application s'executant en dehors d'AWS ». Copier les deux valeurs **une seule fois**, AWS ne
   reaffiche jamais la cle secrete.
4. Coller les deux valeurs dans le fichier `.env` a la racine du depot. Ce fichier est dans
   `.gitignore` : il ne partira jamais sur GitHub. Ne jamais les coller dans `.env.example`, dans un
   fichier Terraform, ni dans un message.
5. Pour l'integration continue, les memes deux valeurs iraient dans
   **Settings > Secrets and variables > Actions**, sous les noms `AWS_ACCESS_KEY_ID` et
   `AWS_SECRET_ACCESS_KEY`. **Ce n'est pas fait aujourd'hui, et c'est volontaire** : le workflow
   public tourne contre LocalStack, aucun identifiant reel n'y circule.

## Verifier que la cle a bien les droits, et seulement eux

```bash
# Doit reussir
aws s3 ls s3://amzn-s3-seau/radar/ --region eu-north-1

# Doivent echouer avec AccessDenied : c'est la preuve que les garde-fous fonctionnent
aws s3 ls                                  # ListAllMyBuckets
aws s3 ls s3://amzn-s3-seau/autre-chose/   # hors du prefixe
aws s3 rb s3://amzn-s3-seau                # suppression du compartiment
```

Le diagnostic du projet fait la meme verification et l'ecrit en JSON :

```bash
python -m uv run python -m radar.cli diagnostic
```

## Nettoyer le compartiment

```bash
# Liste ce qui serait supprime, sans rien supprimer
python -m uv run python -m radar.cli nettoyer

# Une seule couche, ou une seule parution
python -m uv run python -m radar.cli nettoyer --couche bronze --confirmer
python -m uv run python -m radar.cli nettoyer --jour 2026-09-22 --confirmer

# Tout le prefixe du projet
python -m uv run python -m radar.cli nettoyer --confirmer
```

La fonction refuse par construction tout prefixe qui ne commence pas par celui du projet, et ne
supprime rien sans `--confirmer`. Cinq tests couvrent ces deux garde-fous (`tests/test_nettoyage.py`).

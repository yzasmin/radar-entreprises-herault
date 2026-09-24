Ce que fait `politique-minimale.json`, ce qu'il ne fait pas, et comment l'attacher a la cle du graphe.

# Droits minimaux de la cle applicative

Le fichier `infra/politique-minimale.json` est pret a coller dans la console IAM
(**IAM > Politiques > Creer une politique > onglet JSON**). Il ne contient aucun
identifiant : c'est une description de droits, pas un secret.

## Ce que la politique autorise, et rien d'autre

| Action | Sur quoi | Pourquoi le graphe en a besoin |
| --- | --- | --- |
| `s3:ListBucket` | `arn:aws:s3:::amzn-s3-seau`, **uniquement sous le prefixe `radar/`** | Lister les partitions deja ecrites, construire la fenetre glissante, compter les objets |
| `s3:GetBucketLocation` | le meme compartiment | boto3 et DuckDB determinent la region du compartiment avant la premiere lecture |
| `s3:GetObject` | `arn:aws:s3:::amzn-s3-seau/radar/*` | Relire les couches bronze et argent entre deux taches |
| `s3:PutObject` | idem | Ecrire les couches bronze, argent, or, les rapports de qualite et la publication |
| `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts` | idem | Nettoyer un envoi interrompu ; boto3 passe en envoi fractionne au-dela de 8 Mo |

## Ce que la politique interdit explicitement

- **Aucune suppression.** `s3:DeleteObject` et `s3:DeleteBucket` sont absents. Le graphe ecrase une
  partition en la reecrivant (`PutObject` sur la meme cle), il n'a jamais besoin de supprimer.
  Une cle qui ne sait pas supprimer ne peut pas vider un compartiment par accident.
- **Aucun droit hors du prefixe `radar/`.** La troisieme instruction est un `Deny` avec `NotResource` :
  meme si une politique plus large etait attachee un jour au meme utilisateur, tout ce qui sort du
  compartiment et de son prefixe reste refuse. Dans IAM, un `Deny` explicite l'emporte toujours.
- **Aucun autre service.** Ni IAM, ni EC2, ni Lambda, ni CloudWatch. La politique ne parle que de S3.
- **Aucune creation de compartiment.** `s3:CreateBucket` est absent : le compartiment existe deja et
  la cle applicative n'a pas a pouvoir en creer.

## Athena et Glue : pas encore, et volontairement

La politique livree ici couvre **le chemin S3 plus DuckDB**, qui est la configuration publiee.
Le jour ou Athena est branche (`RADAR_MOTEUR=athena`), il faut ajouter les deux blocs ecrits dans
`infra/terraform/iam.tf` : `athena:StartQueryExecution` et les quatre actions de lecture associees sur
le seul workgroup du projet, puis les actions `glue:Get*` et `glue:CreateTable` sur la seule base
`radar_entreprises`. Ils y sont deja, en Terraform, prets a appliquer. Tant qu'Athena n'est pas
utilise, ces droits ne servent a rien et ne sont donc pas accordes.

## Marche a suivre, une fois la politique creee

1. **IAM > Utilisateurs > Creer un utilisateur**, nom `radar-entreprises-pipeline`,
   **sans acces a la console** : cet utilisateur n'est qu'une cle de programme.
2. Attacher la politique creee a partir de `politique-minimale.json`.
3. **Onglet Informations d'identification de securite > Creer une cle d'acces**, cas d'usage
   « Application s'executant en dehors d'AWS ». Copier les deux valeurs **une seule fois**, AWS ne
   reaffiche jamais la cle secrete.
4. Coller les deux valeurs dans le fichier `.env` a la racine du depot. Ce fichier est dans
   `.gitignore` : il ne partira jamais sur GitHub. Ne jamais les coller dans `.env.example`,
   dans un fichier Terraform, ni dans un message.
5. Pour l'integration continue, les memes deux valeurs vont dans
   **Settings > Secrets and variables > Actions** du depot, sous les noms `AWS_ACCESS_KEY_ID` et
   `AWS_SECRET_ACCESS_KEY`. Le workflow publie ne les utilise pas aujourd'hui : il tourne contre
   LocalStack.

## Verifier que la cle a bien les droits, et seulement eux

```bash
# Doit reussir
aws s3 ls s3://amzn-s3-seau/radar/ --region eu-west-3

# Doit echouer avec AccessDenied : c'est la preuve que le garde-fou fonctionne
aws s3 ls s3://amzn-s3-seau/autre-chose/ --region eu-west-3
aws s3 rb s3://amzn-s3-seau --region eu-west-3
```

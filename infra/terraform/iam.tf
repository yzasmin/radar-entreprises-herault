# Droits minimaux de la cle applicative.
#
# Regle appliquee ici : aucune action avec `*` en ressource sur S3, aucun
# `s3:*`, aucun `AmazonS3FullAccess`. Le graphe ecrit sous un seul prefixe d'un
# seul seau, lit ce meme prefixe, et interroge une seule base du catalogue.
#
# Terraform cree l'utilisateur et la politique, mais **ne cree pas la cle
# d'acces** : une cle creee par Terraform finirait en clair dans le fichier
# d'etat. Elle se cree a la main dans la console IAM, une fois, et se range
# dans un `.env` ignore par git ou dans un secret GitHub.

data "aws_iam_policy_document" "radar_donnees" {
  # 1. Lister le seau, mais seulement le prefixe du projet.
  statement {
    sid       = "ListerLePrefixeDuProjet"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.seau_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.prefixe}/*", "${var.prefixe}"]
    }
  }

  # 2. Lire et ecrire les objets, uniquement sous ce prefixe.
  statement {
    sid    = "LireEtEcrireLesObjets"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${local.seau_arn}/${var.prefixe}/*"]
  }

  # 3. Athena : le seul workgroup du projet.
  statement {
    sid    = "InterrogerAvecAthena"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:StopQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
    ]
    resources = [aws_athena_workgroup.radar.arn]
  }

  # 4. Glue : lecture des tables, creation et mise a jour dans la seule base du projet.
  statement {
    sid    = "CatalogueGlueDuProjet"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:BatchCreatePartition",
    ]
    resources = [
      "arn:aws:glue:${var.region}:${local.compte}:catalog",
      "arn:aws:glue:${var.region}:${local.compte}:database/${var.base_catalogue}",
      "arn:aws:glue:${var.region}:${local.compte}:table/${var.base_catalogue}/*",
    ]
  }
}

resource "aws_iam_policy" "radar" {
  name        = "${var.nom_seau}-pipeline"
  description = "Droits minimaux du graphe Airflow du radar economique de l'Herault"
  policy      = data.aws_iam_policy_document.radar_donnees.json
}

resource "aws_iam_user" "radar" {
  name = "${var.nom_seau}-pipeline"
  # Pas de chemin de connexion a la console : cet utilisateur n'est qu'une cle
  # de programme.
  tags = {
    usage = "cle applicative du graphe Airflow"
  }
}

resource "aws_iam_user_policy_attachment" "radar" {
  user       = aws_iam_user.radar.name
  policy_arn = aws_iam_policy.radar.arn
}

# Refus explicite de tout ce qui sort du prefixe : ceinture et bretelles, au cas
# ou une politique plus large serait attachee un jour au meme utilisateur.
data "aws_iam_policy_document" "radar_refus" {
  statement {
    sid       = "RefuserHorsDuPrefixe"
    effect    = "Deny"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.seau_arn}/*"]
    condition {
      test     = "StringNotLike"
      variable = "s3:prefix"
      values   = ["${var.prefixe}/*"]
    }
  }
}

resource "aws_iam_policy" "radar_refus" {
  name        = "${var.nom_seau}-garde-fou"
  description = "Refuse tout acces objet hors du prefixe du projet"
  policy      = data.aws_iam_policy_document.radar_refus.json
}

resource "aws_iam_user_policy_attachment" "radar_refus" {
  user       = aws_iam_user.radar.name
  policy_arn = aws_iam_policy.radar_refus.arn
}

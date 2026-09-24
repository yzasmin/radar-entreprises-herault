terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      projet      = "radar-entreprises-herault"
      responsable = "yasmina-saoud"
      gere_par    = "terraform"
    }
  }
}

data "aws_caller_identity" "courant" {}

# Le compartiment peut preexister : il a ete cree a la main dans la console avant
# que l'infrastructure ne soit decrite. Deux cas, un seul nom.
#  - compartiment_existant = true  : Terraform le lit, ne le cree pas, ne le detruit pas.
#  - compartiment_existant = false : Terraform le cree entierement.
# Pour passer du premier cas au second sans rien perdre :
#   terraform import 'aws_s3_bucket.radar[0]' amzn-s3-seau
resource "aws_s3_bucket" "radar" {
  count         = var.compartiment_existant ? 0 : 1
  bucket        = var.nom_seau
  force_destroy = var.autoriser_destruction
}

data "aws_s3_bucket" "radar" {
  count  = var.compartiment_existant ? 1 : 0
  bucket = var.nom_seau
}

locals {
  compte    = data.aws_caller_identity.courant.account_id
  seau_nom  = var.compartiment_existant ? data.aws_s3_bucket.radar[0].id : aws_s3_bucket.radar[0].id
  seau_arn  = var.compartiment_existant ? data.aws_s3_bucket.radar[0].arn : aws_s3_bucket.radar[0].arn
  racine_s3 = "s3://${local.seau_nom}/${var.prefixe}"
}

# ---------------------------------------------------------------------------
# Reglages du compartiment : appliques dans les deux cas
# ---------------------------------------------------------------------------

# Aucun acces public, jamais : les quatre verrous sont poses explicitement.
resource "aws_s3_bucket_public_access_block" "radar" {
  bucket                  = local.seau_nom
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "radar" {
  bucket = local.seau_nom
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "radar" {
  bucket = local.seau_nom
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "radar" {
  bucket = local.seau_nom
  versioning_configuration {
    status = "Enabled"
  }
}

# Les resultats Athena et les versions anciennes ne servent qu'au debogage :
# on les laisse expirer pour que le cout ne derive pas.
resource "aws_s3_bucket_lifecycle_configuration" "radar" {
  bucket = local.seau_nom

  rule {
    id     = "expirer-resultats-athena"
    status = "Enabled"
    filter {
      prefix = "${var.prefixe}/athena-results/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "purger-versions-anciennes"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# Catalogue et moteur de requete
# ---------------------------------------------------------------------------

resource "aws_glue_catalog_database" "radar" {
  name        = var.base_catalogue
  description = "Radar economique de l'Herault : couches argent et or"
}

resource "aws_athena_workgroup" "radar" {
  name = var.workgroup

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    # Garde-fou de cout : une requete qui balaierait plus que cette limite echoue.
    bytes_scanned_cutoff_per_query = var.octets_max_par_requete

    result_configuration {
      output_location = "${local.racine_s3}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

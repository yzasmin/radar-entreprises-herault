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

locals {
  compte = data.aws_caller_identity.courant.account_id
  # Un seau S3 est global : on suffixe par le compte pour eviter la collision de nom.
  seau = "${var.nom_seau}-${local.compte}"
}

# ---------------------------------------------------------------------------
# Stockage : un seul seau, trois couches, plus les resultats Athena
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "radar" {
  bucket        = local.seau
  force_destroy = var.autoriser_destruction
}

# Aucun acces public, jamais : les quatre verrous sont poses explicitement.
resource "aws_s3_bucket_public_access_block" "radar" {
  bucket                  = aws_s3_bucket.radar.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "radar" {
  bucket = aws_s3_bucket.radar.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "radar" {
  bucket = aws_s3_bucket.radar.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "radar" {
  bucket = aws_s3_bucket.radar.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Les resultats Athena et les versions anciennes ne servent qu'au debogage :
# on les laisse expirer pour que le cout ne derive pas.
resource "aws_s3_bucket_lifecycle_configuration" "radar" {
  bucket = aws_s3_bucket.radar.id

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
      output_location = "s3://${aws_s3_bucket.radar.bucket}/${var.prefixe}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

output "seau" {
  description = "Nom du seau S3 a reporter dans RADAR_BUCKET."
  value       = aws_s3_bucket.radar.bucket
}

output "prefixe" {
  description = "Prefixe des objets, a reporter dans RADAR_PREFIXE."
  value       = var.prefixe
}

output "base_catalogue" {
  description = "Base Glue, a reporter dans RADAR_GLUE_DB."
  value       = aws_glue_catalog_database.radar.name
}

output "workgroup_athena" {
  description = "Workgroup Athena, a reporter dans RADAR_ATHENA_WORKGROUP."
  value       = aws_athena_workgroup.radar.name
}

output "utilisateur_iam" {
  description = "Utilisateur IAM porteur des droits minimaux. Creer sa cle d'acces a la main dans la console, jamais par Terraform."
  value       = aws_iam_user.radar.name
}

output "politiques_attachees" {
  description = "Les deux politiques attachees a l'utilisateur : droits minimaux et garde-fou de refus."
  value       = [aws_iam_policy.radar.arn, aws_iam_policy.radar_refus.arn]
}

output "variables_a_exporter" {
  description = "Bloc pret a coller dans le fichier .env, apres creation de la cle d'acces."
  value = join("\n", [
    "RADAR_BUCKET=${aws_s3_bucket.radar.bucket}",
    "RADAR_PREFIXE=${var.prefixe}",
    "RADAR_GLUE_DB=${aws_glue_catalog_database.radar.name}",
    "RADAR_ATHENA_WORKGROUP=${aws_athena_workgroup.radar.name}",
    "RADAR_MOTEUR=athena",
    "AWS_REGION=${var.region}",
    "# AWS_ENDPOINT_URL doit rester vide : c'est ce qui fait basculer le code sur le vrai AWS.",
    "AWS_ENDPOINT_URL=",
  ])
}

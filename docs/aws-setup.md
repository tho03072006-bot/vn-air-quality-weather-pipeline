# AWS S3 setup and cost guardrails

Use a dedicated S3 bucket and a least-privilege IAM principal. Do not store AWS
access keys in Git, Airflow DAGs, Docker images, or screenshots.

## Bucket controls

1. Create one bucket in the region configured by `AWS_REGION`.
2. Keep Block Public Access enabled.
3. Enable default server-side encryption (SSE-S3 is sufficient for this MVP).
4. Enable bucket versioning if you want an additional recovery layer.
5. Add a lifecycle rule only after measuring retention needs.

Minimum application permissions should be limited to the project prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/raw/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET",
      "Condition": {"StringLike": {"s3:prefix": ["raw/*"]}}
    }
  ]
}
```

Use an AWS profile locally or an IAM role in hosted environments. Test identity
with `aws sts get-caller-identity`; this does not print the secret access key.

## Cost safety

S3 is usage-priced and should not be described as permanently free. Create an
AWS Budget with email alerts at a small actual-cost threshold and a forecasted
threshold before enabling `RAW_BACKEND=s3`. Review the current AWS Free Tier and
S3 pricing pages because offers and request prices can change.

Official references:

- https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html
- https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html

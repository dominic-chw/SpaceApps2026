"""Entry point: run the pipeline end to end. Reads matcher (knn|deep|hybrid) from config.yaml; deep falls back to kNN with a warning. Writes output/*.json per plan s3.
Step 10.
"""

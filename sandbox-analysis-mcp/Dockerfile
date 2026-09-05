FROM opensandbox/code-interpreter@sha256:133a3c1720dd52291a019740c2987e7164ea6de79e23d8198798e58950ae2e6e

ENV PYTHONPATH=/opt/ask-o11y \
    UV_HTTP_TIMEOUT=120

RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk=1:20230817+repack1-3 && rm -rf /var/lib/apt/lists/*

RUN /bin/bash -lc 'source /opt/code-interpreter/code-interpreter-env.sh python 3.14 && uv pip install --break-system-packages --python "$(command -v python)" \
    numpy==2.4.6 \
    scipy==1.18.0 \
    pandas==3.0.5 \
    openpyxl==3.1.5 \
    matplotlib==3.11.1 \
    seaborn==0.13.2 \
    plotly==6.9.0 \
    scikit-learn==1.9.0 \
    statsmodels==0.14.6 \
    shap==0.52.0 \
    catboost==1.2.10 \
    xgboost-cpu==3.4.0 \
    lightgbm==4.7.0 \
    imbalanced-learn==0.14.2 \
    optuna==4.9.0 && python -c "import warnings; import matplotlib; matplotlib.use(\"Agg\"); matplotlib.rcParams[\"font.sans-serif\"]=[\"Noto Sans CJK TC\", \"DejaVu Sans\"]; import matplotlib.pyplot as plt; fig,ax=plt.subplots(); ax.set_title(\"熱耗率相關性\"); c=warnings.catch_warnings(record=True); caught=c.__enter__(); fig.canvas.draw(); c.__exit__(None,None,None); assert not any(\"Glyph\" in str(w.message) for w in caught)"'

COPY ml_plotly_contract.py /opt/ask-o11y/ml_plotly_contract.py
COPY capture.py /opt/ask-o11y/capture.py
COPY ml_preprocessing.py /opt/ask-o11y/ml_preprocessing.py
COPY ml_execution.py /opt/ask-o11y/ml_execution.py
COPY ml_autoresearch.py /opt/ask-o11y/ml_autoresearch.py
COPY ml_regression.py /opt/ask-o11y/ml_regression.py
COPY ml_presentation.py /opt/ask-o11y/ml_presentation.py
COPY data_profile.py /opt/ask-o11y/data_profile.py

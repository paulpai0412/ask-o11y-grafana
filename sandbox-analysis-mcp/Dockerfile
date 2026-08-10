FROM opensandbox/code-interpreter@sha256:133a3c1720dd52291a019740c2987e7164ea6de79e23d8198798e58950ae2e6e

COPY capture.py /opt/ask-o11y/capture.py
ENV PYTHONPATH=/opt/ask-o11y

RUN /bin/bash -lc 'source /opt/code-interpreter/code-interpreter-env.sh python 3.14 && uv pip install --break-system-packages --python "$(command -v python)" \
    numpy==2.4.6 \
    scipy==1.18.0 \
    pandas==3.0.5 \
    matplotlib==3.11.1 \
    seaborn==0.13.2 \
    plotly==6.9.0 \
    scikit-learn==1.9.0 \
    statsmodels==0.14.6 \
    shap==0.52.0 \
    xgboost-cpu==3.4.0 \
    lightgbm==4.7.0 \
    imbalanced-learn==0.14.2 \
    optuna==4.9.0'

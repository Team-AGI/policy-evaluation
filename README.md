# Policy Evaluation

MCP 보안 정책의 예비 평가 실행 프로그램입니다. 보고서의 실제 8개 사례와 연결된 위험 분석 및 보안 요구사항을 포함합니다. Python 3.11 이상이 필요하며 별도 패키지 설치는 없습니다.

- 기본 평가: G-Eval을 참고한 응집성, 사실적 일관성, 유창성, 관련성
- 추가 평가: FLASK를 참고한 사실성, 논리적 견고성, 완전성
- 모델: Claude Opus 5.5, DeepSeek-V4.1-Flash-1017, GPT-6 Luna

## 실행

1. 프로젝트 폴더에서 `.env.example`을 `.env`로 복사하고 실행할 모델의 API 키를 입력합니다. 기본 실행에는 세 제공사의 키가 필요합니다.
2. 아래 명령을 순서대로 실행합니다.

```powershell
python run_pilot.py --dry-run
python run_pilot.py
```

첫 명령은 API를 호출하지 않습니다. 실제 실행의 기본 요청 수는 **8개 사례 × 3개 모델 × 2개 평가 × 1라운드 = 48회**이며 재시도에는 추가 비용이 발생할 수 있습니다. 모델 ID, 라운드 수와 응답 한도는 `config.toml`에서 변경합니다.

### 일부 모델만 평가

`--models` 뒤에 실행할 모델을 지정합니다. 여러 모델은 공백으로 구분하며, `.env`에는 선택한 모델의 API 키만 입력하면 됩니다.

| 모델 | 지정 값 |
| --- | --- |
| Claude Opus 5.5 | `opus` |
| DeepSeek-V4.1-Flash-1017 | `deepseek` |
| GPT-6 Luna | `gpt` |

```powershell
# GPT만 평가
python run_pilot.py --models gpt

# Opus와 DeepSeek만 평가
python run_pilot.py --models opus deepseek
```

기본 조건에서 모델 하나는 16회, 두 모델은 32회 호출합니다. 일부 모델만 실행한 경우 모델별 결과는 `model_scores.csv`에서 확인하며, 전체 가중 종합점수는 산출하지 않습니다.

### 실행 범위와 라운드

사례와 평가 종류를 제한하거나 3라운드를 수행하려면 다음과 같이 지정합니다.

```powershell
python run_pilot.py --cases PIL-01 --models gpt --tasks basic
python run_pilot.py --rounds 3
```

## 결과 확인

실행할 때 표시되는 `outputs/pilot_...` 폴더를 확인합니다.

| 파일 | 내용 |
| --- | --- |
| `summary.csv` | 기본 평가 100점 환산 점수와 추가 평가의 기준별 1~5점 |
| `model_scores.csv` | 모델별 원점수, 판단 근거, 보완 사항과 오류 |
| `case_comparison.csv` | 원본과 대조 사례의 점수 비교 및 담당자 검토란 |
| `usage.csv` | 시도별 토큰 사용량 |
| `requests/`, `attempts/` | 입력과 API 원본 응답 및 재시도 기록 |

세 모델에 50%, 25%, 25%의 가중치를 적용합니다. 일부 모델만 실행하거나 자료가 부족하면 가중 종합점수는 비워 둡니다. 추가 평가의 세 점수는 서로 합산하지 않습니다.

오류가 있거나 실행을 중단했다면 표시된 폴더 경로로 이어 실행합니다. 완료된 응답과 자료 부족 응답은 유지합니다. 자료나 지시문을 바꿨다면 새 실행을 시작합니다.

```powershell
python run_pilot.py --resume "outputs/pilot_실행폴더"
```

## 개발 및 참고

```powershell
python -m unittest discover -s tests -v
```

대조 사례의 예상 문제는 `review/expected_findings.json`에 있으며 모델에는 제공하지 않습니다. 기준의 적용 내용과 원본 ID 대응은 [설계 및 출처](docs/design.md)에 정리했습니다. `.env`와 실행 결과는 Git에서 제외됩니다.

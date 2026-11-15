# 구현 범위와 적용 근거

## 평가 구성

정책 검증 설계 보고서 v3.7의 지시문과 8개 예비 평가 사례를 실행 가능한 형태로 구성했다. 기본 평가와 추가 평가는 별도 API 요청이며 서로의 점수나 의견을 제공하지 않는다. 매 요청은 새로운 문맥에서 정책 하나만 평가한다. 근거 자료는 연결된 위험 분석과 보안 요구사항이며, 관련 정책은 상충 여부와 공통 내용의 확인에 사용한다.

기본 설정은 예비 평가 1라운드다. `--rounds 3`이면 같은 자료를 독립적으로 3번 평가하며 총 144개 요청이 발생한다. 재시도는 새로운 라운드가 아니다. 이 프로그램은 예비 평가를 실행하며 정책을 자동 수정하거나 목표 점수에 도달할 때까지 재호출하지 않는다.

## 기준과 지시문

| 출처 | 채택한 부분 | 이 구현의 적용 |
| --- | --- | --- |
| G-Eval, EMNLP 2023 | 기준 정의와 상세 평가 단계를 제공하는 평가 구성 | 보고서에서 확정한 기본 평가 지시문을 모든 모델에 동일하게 제공. 단계 생성 API를 실행할 때마다 호출하지 않음 |
| G-Eval 및 보고서의 SummEval 척도 적용 | 응집성, 사실적 일관성, 유창성, 관련성 | 보고서에 따라 네 기준 모두 1~5점 정수 사용. G-Eval의 점수 토큰 확률 가중 방식은 구현하지 않음 |
| FLASK, ICLR 2024 | Factuality, Logical Robustness, Completeness의 정의와 기준별 1~5점 채점표 | LLM 응답 대신 정책 본문을 평가. 원래 지시문 및 참조 답변의 역할을 정책 목적과 연결된 위험 및 요구사항으로 구성 |
| NIST SP 800-115 제3.1절과 표 3-1 | 문서의 정확성과 완전성 검토 | 정책의 기술적 설명과 누락을 대조하는 지시문 |
| NIST SP 800-12 Rev.1 제5.3.2절과 제5.4절 | 정책 구성요소, 보안 목표와 규칙 | 주체, 대상, 조건, 조치, 책임, 예외와 승인 조건에 관한 검토 질문 |
| NIST SP 800-53A Rev.5 제2.4절과 제3.2절 | 검토 대상과 자료 및 판단 사항을 구분하는 구조 | 검토 범위와 근거 자료의 명시 |
| AGREE II 매뉴얼 | 정의, 확인할 위치와 판단 안내를 함께 전달하는 구조 | 전문가 검토 안내 형태의 기준별 확인 사항. 의료 분야의 항목과 점수 체계는 사용하지 않음 |

FLASK 채점표는 공식 저장소의 세 기준을 한국어로 요약하여 정책 대상에 적용했다. 사실성의 근거는 제공 자료로 판단하며 정책 문장 자체에 인용이 없다는 이유로 감점하지 않는다. 완전성의 세부 설명과 예시는 정책 준수에 필요한 내용으로 해석하며 모든 정책에 예시를 요구하지 않는다. 논리적 견고성의 예외 상황은 정책 적용 범위의 조건으로 해석한다. 이 적용과 보고서의 지시문을 예비 평가에서 확인한다. 새 정책용 1점, 3점, 5점 조건을 원 연구의 조건으로 제시하지 않는다.

## 사례와 원본 연결

| 사례 | 정책 | 구성 |
| --- | --- | --- |
| PIL-01 | POL-I-08 | 원본: 로컬 서버 권한과 격리 예외 |
| PIL-02 | POL-R-06 | 원본: 토큰 전달 및 재사용 금지 |
| PIL-03 | POL-E-21 | 원본: 확인 결과의 작업 일치와 일회 사용 |
| PIL-04 | POL-O-08 | 원본: 취약점 조치 기한과 대체 조치 |
| PIL-05 | POL-D-07 | 원본: 외부 제공자의 자료 삭제 및 반환 확인 |
| PIL-06 | POL-R-06 | 대조: 토큰 유효 범위의 사실 오류 |
| PIL-07 | POL-E-21 | 대조: 일회 사용과 반복 사용의 모순 |
| PIL-08 | POL-O-08 | 대조: 패치가 없는 경우의 대체 조치 삭제 |

원본 정책은 제공된 정책 시트에서 그대로 추출했다. 관련 정책 7개도 함께 포함한다. 보안 위험과 요구사항은 제공된 `MCP 위험 분석 및 보안 요구사항 도출.xlsx`의 해당 시트에서 추출했다. 출처의 파일 해시와 행 위치는 `data/provenance.json` 및 각 레코드의 `source`에 보존했다. 외부 표준 전문이나 원본 엑셀 파일을 모델에 전송하지 않는다.

정책 시트와 근거 문서의 ID 체계가 달라 선정한 정책의 요구사항 내용을 대조하여 아래와 같이 연결했다. 실행 중 행 번호로 추측하여 연결하지 않는다. 모델과 결과에는 근거 원본의 숫자형 ID를 사용한다.

| 정책 | 정책 시트의 연결 ID | 근거 원본의 요구사항 ID | 위험 ID |
| --- | --- | --- | --- |
| POL-I-08 | SR-I-09 | SR-008 | RSK-07 |
| POL-R-06 | SR-R-06 | SR-017 | RSK-13 |
| POL-E-21 | SR-E-21 | SR-054 | RSK-39 |
| POL-O-08 | SR-O-08 | SR-070 | RSK-49 |
| POL-D-07 | SR-D-07 | SR-088 | RSK-64 |

모델 입력은 허용된 필드만 조립한다. 대조 사례라는 표시, 예상 결함과 원본의 동일 정책 ID 본문은 대조 사례 요청에 넣지 않는다. 같은 정책 ID를 쓰는 원본과 대조 사례의 결과는 사례 ID로 분리한다.

## 집계와 담당자 확인

모델 가중치는 보고서에 따라 Opus 0.50, DeepSeek 0.25, GPT 0.25다. 기본 평가의 모델 점수는 `(네 기준 평균 - 1) / 4 * 100`으로 계산하며 모델 가중 평균을 산출한다. 기준별 원점수에도 같은 가중치를 적용한다. FLASK는 기준별 1~5점의 가중 평균만 산출하고 종합점수를 만들지 않는다. 여러 라운드의 대표점수는 설정된 모든 라운드가 완료된 경우에만 평균으로 계산한다. 누락된 모델이나 점수를 0점으로 대체하거나 가중치를 재조정하지 않는다.

50/25/25의 배정과 100점 환산 및 목표 점수는 보고서의 운영 설계다. G-Eval이나 FLASK가 이 모델 조합과 가중치, 정책의 90점 합격선을 검증한 것으로 표시하지 않는다. 예비 평가에서는 합격을 자동 판정하지 않고 원점수, 판단 근거와 보완 사항을 확인한다. 최솟값과 최댓값은 기술 통계이며 이를 이용한 안정성 합격 조건은 구현하지 않았다.

담당자는 `case_comparison.csv`에서 예상 문제의 식별 여부, 기준별 점수와 판단 근거의 대응, 원본과 대조 사례의 차이를 확인한다. 원본 5개가 반드시 5점이어야 한다는 정답은 두지 않는다. 예상 문제를 놓쳤거나 다른 문제를 제기했다면 응답 근거를 보고 지시문과 자료를 검토한다. 변경 후에는 새 실행에서 재평가한다. 평가 근거를 다른 LLM이 다시 판정하는 단계는 두지 않는다.

## 응답과 재실행

JSON 필드, 정책 ID, 기준명, 정수 범위, null 사유와 제공된 ID의 포함 여부만 프로그램으로 확인한다. 인용 ID가 판단을 실제로 뒷받침하는지는 별도 LLM으로 검증하지 않는다. 자료 부족은 `insufficient_material`로 구분하고 자동 재시도하지 않는다.

출력이 잘렸는지는 제공사의 종료 신호로 먼저 확인한다. 잘린 경우 출력 한도를 설정의 상한까지 늘려 같은 요청을 재시도한다. 통신 오류, 일시적인 API 오류, 빈 응답과 형식 오류는 설정한 횟수까지 재시도한다. 인증이나 모델 ID 오류 등은 같은 모델의 이후 호출을 보류한다. 거부 응답은 자동 반복하지 않는다. 타임아웃 뒤에는 제공사에서 이미 처리했을 가능성이 있어 중복 청구가 발생할 수 있다.

시도마다 요청 내용과 원본 응답을 저장한다. 완료된 응답은 재사용하며 실패했거나 실행하지 못한 요청만 이어 실행한다. 입력, 지시문, 코드와 모델 설정의 해시를 확인하여 다른 조건의 결과를 같은 실행에 섞지 않는다. `max_attempts`는 명령 한 번을 실행할 때의 요청별 최대 시도 횟수다. `--resume`으로 다시 실행하면 실패한 요청에 추가 시도 기회를 부여한다.

토큰 상한과 재시도 횟수는 조정 가능한 운영 기본값이다. 문자 수를 토큰 수로 취급하지 않는다. `usage.csv`는 실패와 재시도를 포함하여 제공사가 반환한 사용량을 기록한다. 단가를 코드에 고정한 비용 추정은 하지 않는다. API 키와 인증 헤더는 저장하지 않는다.

## 모델과 API

| 표시 이름 | 실제 API ID | API |
| --- | --- | --- |
| Claude Opus 5.5 | claude-opus-5-5 | Anthropic Messages |
| DeepSeek-V4.1-Flash-1017 | deepseek-flash | DeepSeek Chat Completions |
| GPT-6 Luna | gpt-6-luna | OpenAI Responses |

2026-11-15 공식 문서에서 API ID를 확인했다. 제공사 별칭이 가리키는 버전은 변경될 수 있으므로 요청 ID와 반환 모델명을 기록한다. 계정별 모델 접근 권한과 실제 호출의 성공 여부는 API 키를 가진 실행자가 확인해야 한다. 세 모델에 임의의 공통 temperature나 seed를 강제하지 않고 제공사가 지원하는 추론 설정을 사용한다.

## 출처

- [G-Eval, EMNLP 2023](https://aclanthology.org/2023.emnlp-main.153/)
- [FLASK, ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f41b4a6b202adcd8e150a9d4f124d8f6-Abstract-Conference.html)
- [FLASK 공식 채점표](https://github.com/kaistAI/FLASK/blob/main/metadata_annotation/skillset/src/skillset_description.json)
- [NIST SP 800-115](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-115.pdf)
- [NIST SP 800-12 Rev.1](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-12r1.pdf)
- [NIST SP 800-53A Rev.5](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53Ar5.pdf)
- [AGREE II 매뉴얼](https://www.agreetrust.org/wp-content/uploads/2017/12/AGREE-II-Users-Manual-and-23-item-Instrument-2009-Update-2017.pdf)
- [정책 시트](https://docs.google.com/spreadsheets/d/1NGFWTI_TNnUiDPHupOIfEOyfcB9h5ecM/edit?gid=1487612037)
- [Claude 모델과 ID](https://platform.claude.com/docs/en/models/overview)
- [Claude Messages API](https://platform.claude.com/docs/en/api/messages)
- [DeepSeek 모델과 ID](https://api-docs.deepseek.com/quick_start/pricing/)
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

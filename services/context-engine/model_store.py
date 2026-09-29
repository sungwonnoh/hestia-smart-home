from typing import Optional

from kde_model import KDEModel


class ModelStore:
    """
    Context Engine에서 사용하는
    최신 학습 모델을 메모리에 보관한다.
    """

    def __init__(self):
        self._kde: Optional[KDEModel] = None

    def set_kde(
        self,
        model: KDEModel,
    ) -> None:
        """
        최신 KDE 모델로 교체한다.
        """

        self._kde = model

    def get_kde(
        self,
    ) -> Optional[KDEModel]:
        """
        현재 KDE 모델을 반환한다.

        아직 모델을 받지 않았다면 None.
        """

        return self._kde

    def has_kde(
        self,
    ) -> bool:
        return self._kde is not None
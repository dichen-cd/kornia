from typing import Dict, Optional, Tuple, Union, cast

import torch

from kornia.augmentation import random_generator as rg
from kornia.augmentation._2d.geometric.base import GeometricAugmentationBase2D
from kornia.constants import Resample
from kornia.geometry.transform import crop_by_transform_mat, get_perspective_transform


class CenterCrop(GeometricAugmentationBase2D):
    r"""Crop a given image tensor at the center.

    .. image:: _static/img/CenterCrop.png

    Args:
        size: Desired output size (out_h, out_w) of the crop.
            If integer,  out_h = out_w = size.
            If Tuple[int, int], out_h = size[0], out_w = size[1].
        align_corners: interpolation flag.
        resample: The interpolation mode.
        return_transform: if ``True`` return the matrix describing the transformation
            applied to each.
        p: probability of applying the transformation for the whole batch.
        keepdim: whether to keep the output shape the same as input (True) or broadcast it
                        to the batch form (False).
        cropping_mode: The used algorithm to crop. ``slice`` will use advanced slicing to extract the tensor based
                       on the sampled indices. ``resample`` will use `warp_affine` using the affine transformation
                       to extract and resize at once. Use `slice` for efficiency, or `resample` for proper
                       differentiability.

    Shape:
        - Input: :math:`(C, H, W)` or :math:`(B, C, H, W)`, Optional: :math:`(B, 3, 3)`
        - Output: :math:`(B, C, out_h, out_w)`

    .. note::
        This function internally uses :func:`kornia.geometry.transform.crop_by_boxes`.

    Examples:
        >>> rng = torch.manual_seed(0)
        >>> inputs = torch.randn(1, 1, 4, 4)
        >>> inputs
        tensor([[[[-1.1258, -1.1524, -0.2506, -0.4339],
                  [ 0.8487,  0.6920, -0.3160, -2.1152],
                  [ 0.3223, -1.2633,  0.3500,  0.3081],
                  [ 0.1198,  1.2377,  1.1168, -0.2473]]]])
        >>> aug = CenterCrop(2, p=1., cropping_mode="resample")
        >>> out = aug(inputs)
        >>> out
        tensor([[[[ 0.6920, -0.3160],
                  [-1.2633,  0.3500]]]])
        >>> aug.inverse(out, padding_mode="border")
        tensor([[[[ 0.6920,  0.6920, -0.3160, -0.3160],
                  [ 0.6920,  0.6920, -0.3160, -0.3160],
                  [-1.2633, -1.2633,  0.3500,  0.3500],
                  [-1.2633, -1.2633,  0.3500,  0.3500]]]])

    To apply the exact augmenation again, you may take the advantage of the previous parameter state:
        >>> input = torch.randn(1, 3, 32, 32)
        >>> aug = CenterCrop(2, p=1., cropping_mode="resample")
        >>> (aug(input) == aug(input, params=aug._params)).all()
        tensor(True)
    """

    def __init__(
        self,
        size: Union[int, Tuple[int, int]],
        align_corners: bool = True,
        resample: Union[str, int, Resample] = Resample.BILINEAR.name,
        return_transform: bool = False,
        p: float = 1.0,
        keepdim: bool = False,
        cropping_mode: str = "slice",
    ) -> None:
        # same_on_batch is always True for CenterCrop
        # Since PyTorch does not support ragged tensor. So cropping function happens batch-wisely.
        super().__init__(p=1.0, return_transform=return_transform, same_on_batch=True, p_batch=p, keepdim=keepdim)
        if isinstance(size, tuple):
            self.size = (size[0], size[1])
        elif isinstance(size, int):
            self.size = (size, size)
        else:
            raise Exception(f"Invalid size type. Expected (int, tuple(int, int). " f"Got: {type(size)}.")

        self.flags = dict(
            resample=Resample.get(resample), cropping_mode=cropping_mode, align_corners=align_corners, size=self.size
        )

    def generate_parameters(self, batch_shape: torch.Size) -> Dict[str, torch.Tensor]:
        return rg.center_crop_generator(batch_shape[0], batch_shape[-2], batch_shape[-1], self.size, self.device)

    def compute_transformation(self, input: torch.Tensor, params: Dict[str, torch.Tensor]) -> torch.Tensor:
        transform: torch.Tensor = get_perspective_transform(params["src"].to(input), params["dst"].to(input))
        transform = transform.expand(input.shape[0], -1, -1)
        return transform

    def apply_transform(
        self, input: torch.Tensor, params: Dict[str, torch.Tensor], transform: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        if self.flags["cropping_mode"] == "resample":  # uses bilinear interpolation to crop
            transform = cast(torch.Tensor, transform)
            return crop_by_transform_mat(
                input,
                transform[:, :2, :],
                self.size,
                self.flags["resample"].name.lower(),
                "zeros",
                self.flags["align_corners"],
            )
        if self.flags["cropping_mode"] == "slice":  # uses advanced slicing to crop
            # TODO: implement as separated function `crop_and_resize_iterative`
            B, C, _, _ = input.shape
            src = torch.as_tensor(params["src"], device=torch.device("cpu"), dtype=torch.long).numpy()
            x1 = src[:, 0, 0]
            x2 = src[:, 1, 0] + 1
            y1 = src[:, 0, 1]
            y2 = src[:, 3, 1] + 1

            if self.same_on_batch:
                return input[..., y1[0]:y2[0], x1[0]:x2[0]]

            out = torch.empty(B, C, *self.flags["size"], device=input.device, dtype=input.dtype)
            for i in range(B):
                out[i] = input[i : i + 1, :, y1[i]:y2[i], x1[i]:x2[i]]
            return out
        raise NotImplementedError(f"Not supported type: {self.flags['cropping_mode']}.")

    def inverse_transform(
        self,
        input: torch.Tensor,
        transform: Optional[torch.Tensor] = None,
        size: Optional[Tuple[int, int]] = None,
        **kwargs,
    ) -> torch.Tensor:
        if self.flags["cropping_mode"] != "resample":
            raise NotImplementedError(
                f"`inverse` is only applicable for resample cropping mode. Got {self.flags['cropping_mode']}."
            )
        if size is None:
            size = self.size
        mode = self.flags["resample"].name.lower() if "mode" not in kwargs else kwargs["mode"]
        align_corners = self.flags["align_corners"] if "align_corners" not in kwargs else kwargs["align_corners"]
        padding_mode = "zeros" if "padding_mode" not in kwargs else kwargs["padding_mode"]
        transform = cast(torch.Tensor, transform)
        return crop_by_transform_mat(input, transform[:, :2, :], size, mode, padding_mode, align_corners)

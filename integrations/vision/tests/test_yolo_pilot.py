"""Local-detector adapter checks, without installing/downloading a detector."""
import pytest
from integrations.vision.yolo_pilot import normalized_detection


def test_xyxy_to_yxyx_respects_non_square_image_dimensions():
    result=normalized_detection('Computer monitor',[100,50,600,350],1000,500)
    assert result['box_2d']==[100,100,700,600]
    assert result['label']=='Computer monitor'
    assert 'confidence' not in result  # Confidence is stored separately, preserving the common observation schema.


@pytest.mark.parametrize('box', [[-1,0,100,100],[0,0,1001,100],[10,10,5,100],[0,0,20,0]])
def test_invalid_detector_geometry_is_not_silently_repaired(box):
    with pytest.raises(ValueError):
        normalized_detection('Chair',box,1000,500)

"""Known geometry and annotation rules; entirely offline."""
import pytest
from integrations.vision.evaluate import ground_truth, score_predictions


def pred(label='armchair', box=None):
    return {'label':label,'description':'Visible product.','attributes':[],
            'box_2d':box or [0,0,1000,1000],'uncertainty':''}


def gold(label='chair', box=None, number=1):
    return {'canonical':label,'label':label,'id':number,'box_2d':box or [0,0,1000,1000]}


def test_aliases_match_and_a_duplicate_cannot_claim_the_same_gold():
    result=score_predictions([pred(),pred()], [gold()])
    assert (result['tp'],result['fp'],result['fn'])==(1,1,0)
    assert result['precision']==0.5 and result['recall']==1
    assert result['false_positives'][0]['reason'].startswith('duplicate')


def test_invalid_box_is_false_positive_and_leaves_gold_unmatched():
    result=score_predictions([pred(box=[100,500,900,200])],[gold()])
    assert (result['tp'],result['fp'],result['fn'])==(0,1,1)
    assert result['false_positives'][0]['reason']=='invalid box'


def test_prediction_of_an_unannotated_class_is_unscored_not_false_positive():
    result=score_predictions([pred('desk lamp')],[gold('monitor')])
    assert result['tp']==result['fp']==0 and result['fn']==1
    assert result['precision'] is None and len(result['unscored_predictions'])==1


def test_one_to_one_assignment_prioritizes_match_count():
    # Wide prediction can match either gold at IoU=.6; narrow prediction only left.
    targets=[gold(box=[0,0,1000,600]),gold(box=[0,400,1000,1000],number=2)]
    result=score_predictions([pred(),pred(box=[0,0,1000,600])],targets)
    assert (result['tp'],result['fp'],result['fn'])==(2,0,0)
    assert result['mean_matched_iou']==pytest.approx(0.8)


def test_group_boxes_ignore_valid_predictions_but_do_not_hide_invalid_ones():
    result=score_predictions([pred(),pred(box=[10,500,20,100])],[],[gold()])
    assert result['tp']==result['fn']==0 and result['fp']==1
    assert len(result['group_ignored_predictions'])==1


def test_overlapping_teapot_and_kettle_are_not_two_gold_objects():
    def annotation(label):
        return {'label':label,'XMin':'0','YMin':'0','XMax':'1','YMax':'1',
                'Confidence':'1','IsGroupOf':'0','IsDepiction':'0','IsInside':'0'}
    boxes,groups,excluded=ground_truth({'source_annotations':[annotation('Kettle'),annotation('Teapot'),annotation('Building')]})
    assert len(boxes)==1 and boxes[0]['canonical']=='teapot'
    assert len(excluded)==2 and not groups


def test_empty_pipeline_output_has_zero_recall_and_undefined_precision():
    result=score_predictions([], [gold()])
    assert result['precision'] is None and result['recall']==0
